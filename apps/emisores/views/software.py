"""API del software DIAN del emisor."""
from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import servicios as dian
from apps.emisores import models, serializers
from apps.emisores.servicios import (
    crear_nomina_de_prueba,
    crear_notas_ajuste_de_prueba,
    habilitacion,
    motivo_no_puede_emitir,
    sembrar_documentos_de_prueba,
    sembrar_resolucion_de_pruebas,
)
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class SoftwareDianViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    serializer_class = serializers.SoftwareDianSerializer
    queryset = models.SoftwareDian.objects.select_related("emisor")

    def get_queryset(self):
        """Permite filtrar por emisor, por módulo y por el Set de Pruebas.

        ``/api/emisores/software/?emisor=<id>&modulo=<facturacion|nomina>``
        ``&set_pruebas_aceptado=true|false``. Otro valor en
        ``set_pruebas_aceptado`` responde 400.
        """
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        if emisor:
            qs = qs.filter(emisor=emisor)
        modulo = self.request.query_params.get("modulo")
        if modulo:
            if modulo not in models.SoftwareDian.Modulo.values:
                raise ErrorSolicitud(
                    "El filtro 'modulo' tiene que ser uno de: "
                    + ", ".join(models.SoftwareDian.Modulo.values)
                    + f"; se recibió '{modulo}'."
                )
            qs = qs.filter(modulo=modulo)
        aceptado = self.request.query_params.get("set_pruebas_aceptado")
        if aceptado:
            if aceptado.lower() not in ("true", "false"):
                raise ErrorSolicitud(
                    "El filtro 'set_pruebas_aceptado' tiene que ser 'true' o "
                    f"'false'; se recibió '{aceptado}'."
                )
            qs = qs.filter(set_pruebas_aceptado=aceptado.lower() == "true")
        return qs

    # Un software por emisor y operación lo comprueba el serializer, que da el
    # mensaje bueno (con la ruta del que ya existe). Lo de aquí abajo es solo
    # para la carrera: dos altas simultáneas del mismo tipo pasan las dos por
    # esa comprobación y es el índice único quien para a la segunda. Sin esto
    # saldría como un 500.

    def perform_create(self, serializer):
        """Registra el software y le siembra la resolución del Set de Pruebas.

        Registrar el software es el primer paso de una habilitación y sin
        numeración no hay nada que emitir después: dejar los dos pasos
        separados deja al emisor con software y sin poder crear ni un documento
        de prueba, sin que nada lo diga.

        Qué se siembra lo decide el tipo de software, y puede no ser nada: la
        nómina no se numera con resolución. Tampoco se siembra si el emisor ya
        está en producción para esa operación. Ver
        `sembrar_resolucion_de_pruebas`.

        Con la resolución puesta, el de facturación deja además sus facturas de
        prueba en borrador y una nota crédito que anula la primera (se firma
        cuando esa factura esté aceptada), el de documento equivalente sus dos P.O.S. y el de
        nómina sus 10 nóminas con una nota de ajuste cada una (la nota se firma
        cuando su nómina esté aceptada). Son el material del Set de Pruebas y se crean aquí
        por lo mismo que la resolución: para que el emisor no acabe el alta con
        numeración y sin nada que emitir contra ella.

        Va dentro de la misma transacción que el alta: media habilitación es
        peor que ninguna, porque no se ve.
        """
        with transaction.atomic():
            self._guardar(super().perform_create, serializer)
            software = serializer.instance
            resolucion, _ = sembrar_resolucion_de_pruebas(
                software.emisor, software.tipo,
            )
            sembrar_documentos_de_prueba(
                software.emisor, software.tipo, resolucion,
            )

    @action(detail=True, methods=["post"], url_path="crear-nomina-prueba")
    def crear_nomina_prueba(self, request, pk=None):
        """Crea una nómina de prueba en borrador para este software.

        ``POST /api/emisores/software/{id}/crear-nomina-prueba/``, con
        ``{"consecutivo": <n>}`` opcional.

        Es la misma que siembra el alta, pero de una en una: sirve para
        completar un Set de Pruebas al que le faltan nóminas. **Solo sobre un
        software de nómina**; los documentos de prueba de facturación se crean
        desde su resolución, que es quien los numera.

        Sin ``consecutivo`` toma el siguiente libre del emisor para el prefijo
        de pruebas. El periodo de liquidación continúa la serie hacia atrás —la
        regla 90 rechaza dos nóminas del mismo trabajador para el mismo
        periodo—. La nómina no se edita: si hace falta otro periodo, se borra
        el borrador y se crea por ``POST /api/nomina/nomina/`` con el que toque.

        Solo la crea: no la firma ni la envía. Para eso está ``emitir`` de
        ``/api/nomina/nomina/{id}/``, que firma y envía.
        """
        # `get_object` va contra el queryset del mixin, así que un software
        # fuera del alcance no se encuentra (404) en vez de responder 403 y
        # delatar que existe.
        software = self.get_object()

        consecutivo = request.data.get("consecutivo")
        if consecutivo in (None, ""):
            consecutivo = None
        else:
            try:
                consecutivo = int(consecutivo)
            except (TypeError, ValueError):
                raise ErrorSolicitud("El consecutivo debe ser un número entero.")
            if consecutivo < 1:
                raise ErrorSolicitud("El consecutivo debe ser mayor que cero.")

        try:
            nomina = crear_nomina_de_prueba(software, consecutivo)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))

        return Response(
            {
                "id": str(nomina.id),
                "numero": nomina.numero,
                "consecutivo": nomina.consecutivo,
                "estado": nomina.estado.nombre,
                "periodo": [
                    str(nomina.fecha_liquidacion_inicio),
                    str(nomina.fecha_liquidacion_fin),
                ],
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="crear-nota-ajuste-prueba")
    def crear_nota_ajuste_prueba(self, request, pk=None):
        """Crea 11 notas de ajuste de prueba en borrador para este software.

        ``POST /api/emisores/software/{id}/crear-nota-ajuste-prueba/``, sin
        cuerpo.

        Recorre las nóminas del emisor y toma la más reciente que esté
        **aceptada por la DIAN y sin errores**; sobre ella crea las once notas,
        de reemplazo (``TipoNota`` 1) e idénticas a la original. Si no hay
        ninguna así, responde 400 y no crea nada. **Solo sobre un software de
        nómina**, y con el emisor todavía en pruebas.

        Solo las crea: no las firma ni las envía. Para eso está ``emitir`` de
        ``/api/nomina/nomina/{id}/``, que firma y envía.
        """
        software = self.get_object()

        try:
            nomina, notas = crear_notas_ajuste_de_prueba(software)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))

        return Response(
            {
                "nomina_ajustada": {
                    "id": str(nomina.id),
                    "numero": nomina.numero,
                },
                "notas": [
                    {
                        "id": str(nota.id),
                        "numero": nota.numero,
                        "consecutivo": nota.consecutivo,
                        "estado": nota.estado.nombre,
                    }
                    for nota in notas
                ],
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=None,
        responses={200: inline_serializer(
            name="HabilitacionSoftware",
            fields={
                "id": drf_serializers.IntegerField(),
                "terminado": drf_serializers.BooleanField(),
                "motivo": drf_serializers.ChoiceField(choices=habilitacion.MOTIVOS),
                "set_pruebas_aceptado": drf_serializers.BooleanField(),
                "documentos": inline_serializer(
                    name="HabilitacionDocumento",
                    many=True,
                    fields={
                        "id": drf_serializers.UUIDField(),
                        "numero": drf_serializers.CharField(),
                        "tipo": drf_serializers.CharField(),
                        "estado": drf_serializers.CharField(),
                        "error": drf_serializers.CharField(),
                    },
                ),
            },
        )},
    )
    @action(detail=True, methods=["post"])
    def habilitar(self, request, pk=None):
        """Emite el Set de Pruebas y espera el veredicto de la DIAN.

        ``POST /api/emisores/software/{id}/habilitar/``, sin cuerpo. **Solo
        sobre un software de facturación o de nómina** y con el Set de Pruebas
        sin aceptar.

        Trabaja **en la propia petición** (hasta unos 75 segundos) sobre los
        documentos de pruebas del emisor —los que siembra el alta del
        software—:

        - **Facturación**: primero las facturas de venta y después las notas
          crédito. Los borradores se firman con la fecha de hoy —la DIAN la
          exige y pudieron sembrarse otro día—.
        - **Nómina**: primero las nóminas y después las notas de ajuste.

        Firma y envía lo que está en borrador, envía lo firmado y consulta lo
        enviado sin veredicto, cada pocos segundos, hasta que quede aceptado o
        rechazado. Cada nota se emite en cuanto el documento que corrige está
        aceptado, porque lleva su CUFE/CUNE.

        Responde 200 con ``motivo``:

        - ``set_aceptado``: la DIAN aceptó el Set; el software y el emisor
          quedan habilitados. ``terminado: true``.
        - ``sin_pendientes``: todo quedó aceptado o rechazado (una nota cuyo
          documento se rechazó cuenta como terminada). ``terminado: true``.
        - ``con_errores``: lo que queda no se pudo emitir; el motivo va en el
          ``error`` de cada documento.
        - ``dian_sin_respuesta`` o ``tiempo_agotado``: se cortó a mitad. **Se
          vuelve a llamar** y sigue donde quedó, sin reenviar nada.

        Un documento rechazado no se reintenta. Una factura se corrige y se
        pasa a borrador con `reiniciar/`; una nómina se borra y se crea de
        nuevo. Después se vuelve a llamar a este endpoint.
        """
        software = self.get_object()
        conjunto = habilitacion.set_de(software)
        if conjunto is None:
            raise ErrorSolicitud(
                "Solo se habilita por aquí un software de facturación o de "
                f"nómina; este es de {software.get_tipo_display().lower()}."
            )
        if software.set_pruebas_aceptado:
            raise ErrorSolicitud(
                "El Set de Pruebas de este software ya fue aceptado por la DIAN."
            )
        motivo = motivo_no_puede_emitir(software.emisor)
        if motivo:
            raise ErrorSolicitud(motivo)
        if not conjunto.documentos(software.emisor).exists():
            raise ErrorSolicitud(
                "El emisor no tiene documentos de pruebas que emitir para "
                f"{software.get_tipo_display().lower()}. Se crean al registrar "
                "el software."
            )

        motivo, documentos = habilitacion.habilitar(software)
        return Response({
            "id": software.pk,
            "terminado": motivo in habilitacion.MOTIVOS_TERMINADO,
            "motivo": motivo,
            "set_pruebas_aceptado": software.set_pruebas_aceptado,
            "documentos": documentos,
        })

    def perform_destroy(self, instance):
        """Borra el software, salvo que ya esté habilitado o en producción.

        Con el software se va `set_pruebas_aceptado`, que solo marca el backend
        cuando la DIAN acepta el Set de Pruebas: registrarlo de nuevo lo deja
        en falso y no hay forma de recuperarlo por la API. Y en producción,
        borrarlo deja al emisor sin poder emitir esa operación.
        """
        etiqueta = instance.get_tipo_display().lower()
        if instance.set_pruebas_aceptado:
            raise ErrorSolicitud(
                f"El software de {etiqueta} ya tiene el Set de Pruebas "
                f"aceptado por la DIAN y no se puede borrar: la habilitación "
                f"se perdería. Si de verdad hay que rehacerla, desactívelo "
                f"antes (POST .../software/{instance.pk}/desactivar/)."
            )
        if instance.emisor.habilitado_para(instance.tipo):
            raise ErrorSolicitud(
                f"El emisor está habilitado para {etiqueta} y su software no "
                f"se puede borrar: se quedaría sin poder emitir. Para cambiar "
                f"el SoftwareID o el PIN, actualice el que hay (PATCH) en vez "
                f"de borrarlo."
            )
        instance.delete()

    @action(detail=True, methods=["post"])
    def desactivar(self, request, pk=None):
        """Deja el software y al emisor sin habilitar para esa operación.

        ``POST /api/emisores/software/{id}/desactivar/``, sin cuerpo.

        Baja ``set_pruebas_aceptado`` del software y la bandera de habilitación
        del emisor que le corresponde (``habilitado_facturacion``,
        ``habilitado_nomina`` o ``habilitado_documento_equivalente``). Las
        otras operaciones no se tocan. Es lo contrario de lo que hace el
        backend cuando la DIAN acepta el Set de Pruebas, y la única forma de
        deshacerlo por la API: las dos banderas son de solo lectura.

        Con ellas abajo los envíos vuelven al Set de Pruebas, y el software se
        puede modificar o borrar otra vez.

        Es idempotente: sobre un software que ya está sin habilitar responde
        200 igual.
        """
        software = self.get_object()
        emisor = software.emisor
        etiqueta = software.get_tipo_display().lower()

        with transaction.atomic():
            campo = dian.desmarcar_habilitacion(software)

        return Response(
            {
                "id": software.pk,
                "tipo": software.tipo,
                "set_pruebas_aceptado": software.set_pruebas_aceptado,
                "emisor": emisor.pk,
                campo: getattr(emisor, campo),
            }
        )

    def perform_update(self, serializer):
        self._guardar(super().perform_update, serializer)

    @staticmethod
    def _guardar(guardar, serializer):
        try:
            # El savepoint es lo que permite seguir atendiendo la petición:
            # sin él la transacción queda marcada y la siguiente consulta
            # muere con un TransactionManagementError en vez de con su error.
            with transaction.atomic():
                guardar(serializer)
        except IntegrityError:
            raise ErrorSolicitud(
                "El emisor ya tiene un software DIAN de esa operación. Cada "
                "una admite uno: actualice el que hay en vez de registrar otro."
            )
