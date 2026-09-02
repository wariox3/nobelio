"""API del emisor."""
from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.catalogos.models import TipoFactura
from apps.emisores import models, serializers
from apps.emisores.models.emisor import Emisor
from apps.emisores.models.certificado import Certificado
from apps.emisores.servicios import crear_factura_prueba, crear_nomina_prueba
from apps.nucleo.api import ErrorSolicitud
from apps.nucleo.models import Ambiente
from apps.seguridad.alcance import (
    MENSAJE_SIN_CUENTA,
    AlcanceEmisorMixin,
    emisores_permitidos,
    es_staff,
    exigir_alcance,
    exigir_cuenta,
    puede_dar_de_alta,
)
from apps.utilidades.rues import RuesNoDisponible, consultar_detalle


class EmisorViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):

    campo_emisor = "id"

    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion", "nombre_comercial"]

    def get_serializer_class(self):
        """El listado va sin resoluciones; el detalle y las escrituras sí las llevan."""
        if self.action == "list":
            return serializers.EmisorListaSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        # Sin el campo anidado, traerse las resoluciones del listado entero es
        # una consulta que nadie aprovecha.
        consulta = super().get_queryset()
        if self.action == "list":
            consulta = consulta.prefetch_related(None).prefetch_related(
                "responsabilidades"
            ).select_related("municipio")
        return consulta

    def create(self, request, *args, **kwargs):
        # Se comprueba antes de validar el cuerpo: quien no tiene cuenta de la
        # que colgar el emisor merece un 403 claro, no un 400 sobre los campos.
        if not puede_dar_de_alta(request):
            raise PermissionDenied(MENSAJE_SIN_CUENTA)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Para una integración la cuenta ya la puso el default del serializer y
        # `validate_cuenta` impidió que apuntara a otra. Al staff, que no tiene
        # credencial de cuenta, hay que exigírsela.
        cuenta = serializer.validated_data.get("cuenta")
        if not cuenta:
            raise ValidationError({"cuenta": "Es obligatoria para el staff."})
        # Última puerta antes de escribir: el serializer ya lo comprobó, pero la
        # regla se verifica aquí contra `alcance` para que ningún cambio futuro
        # en el serializer pueda abrir un alta en cuenta ajena en silencio.
        exigir_cuenta(self.request, cuenta)
        serializer.save()

    def perform_update(self, serializer):
        # El alcance ya limitó el queryset; esto impide además que un emisor se
        # mueva a otra cuenta desde el cuerpo de la petición. Solo el staff
        # puede cambiarla; si no la indica (PUT sin 'cuenta', donde el default
        # de la credencial es None) se conserva la que ya tenía.
        cuenta = serializer.instance.cuenta
        if es_staff(self.request):
            cuenta = serializer.validated_data.get("cuenta") or cuenta
        serializer.save(cuenta=cuenta)

    @action(detail=False, methods=["get"], url_path="validar-nit")
    def validar_nit(self, request):
        """Valida un NIT contra el RUES y devuelve sus datos para autocompletar.

        ``GET /api/emisores/emisor/validar-nit/?nit=900123456``

        Respuestas:
          - 200 ``{"existe": true, ...datos...}``  si el NIT está en el RUES.
          - 200 ``{"existe": false}``              si no se encuentra.
          - 400 si falta el parámetro ``nit``.
          - 503 si el servicio RUES no está disponible.
        """
        nit = (request.query_params.get("nit") or "").strip()
        if not nit:
            return Response(
                {"detail": "Debe indicar el parámetro 'nit'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            empresa = consultar_detalle(nit)
        except RuesNoDisponible as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if empresa is None:
            return Response({"existe": False, "nit": nit})

        return Response(
            {
                "existe": True,
                "nit": empresa.nit,
                "digito_verificacion": empresa.digito_verificacion,
                "razon_social": empresa.razon_social,
                "estado_matricula": empresa.estado_matricula,
                "activa": empresa.activa,
                "organizacion_juridica": empresa.organizacion_juridica,
                "camara_comercio": empresa.camara_comercio,
                "correo": empresa.correo,
                "direccion": empresa.direccion,
                "telefono": empresa.telefono,
                "actividad_ciiu": empresa.actividad_ciiu,
                "actividad_ciiu_descripcion": empresa.actividad_ciiu_descripcion,
            }
        )

    def _emisor_del_cuerpo(self, request):
        """El emisor que indica el cuerpo de la petición, dentro del alcance.

        Distingue los dos motivos por los que antes se respondía siempre "el
        emisor indicado no existe": que no venga —el caso habitual, y que se
        daba también cuando el cuerpo llegaba sin parsear por faltar el
        Content-Type— y que venga uno que de verdad no está.

        La búsqueda va **acotada al alcance del solicitante**, y no es un
        detalle: mientras se buscaba entre todos los emisores y el alcance se
        comprobaba después, un id ajeno respondía 403 y uno inexistente 400. Esa
        diferencia convierte al endpoint en un oráculo con el que una cuenta
        autenticada puede averiguar qué ids existen en las demás. Ahora las dos
        respuestas son idénticas, que es el mismo criterio de
        ``RelacionDelAlcance``.
        """
        identificador = request.data.get("emisor")
        if identificador in (None, ""):
            raise ErrorSolicitud("Indique el emisor en el campo 'emisor'.")
        alcanzables = emisores_permitidos(request)
        consulta = Emisor.objects.all() if alcanzables is None else alcanzables
        try:
            return consulta.get(pk=identificador)
        except (Emisor.DoesNotExist, TypeError, ValueError):
            raise ErrorSolicitud("El emisor indicado no existe.")

    # Los datos del Set de Pruebas de la DIAN. Son suyos y son públicos: la
    # misma resolución y la misma clave técnica las usa todo el que se habilita.
    # Van aquí arriba, con nombre, en vez de incrustados en el cuerpo del
    # endpoint, para que se vea de un vistazo qué se le está escribiendo al
    # emisor y para que cambiar uno no obligue a leer el flujo entero.
    RESOLUCION_SET_PRUEBAS = {
        "prefijo": "SETP",
        "numero_resolucion": "18760000001",
        "fecha_resolucion": date(2026, 6, 29),
        "rango_desde": 990000000,
        "rango_hasta": 995000000,
        "clave_tecnica": "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c",
        "vigente_desde": date(2019, 1, 19),
        "vigente_hasta": date(2030, 1, 19),
    }

    @action(detail=False, methods=["post"], url_path="crear-habilitacion")
    def crear_habilitacion(self, request):
        """Deja al emisor listo para el Set de Pruebas, en una sola llamada.

        ``POST /api/emisores/emisor/crear-habilitacion/`` con ``emisor`` y los
        campos del software (``tipo``, ``identificador``, ``pin``, y el
        ``test_set_id`` si ya se tiene). Registra el software y le siembra al
        emisor la resolución de numeración del Set de Pruebas.

        Es idempotente a propósito: durante una habilitación se llama varias
        veces, así que un software ya registrado no se duplica y la resolución
        se actualiza en vez de repetirse.

        **Solo sobre un emisor en ambiente de pruebas.** Lo que escribe son
        datos del sandbox de la DIAN —una resolución que no es del emisor y una
        clave técnica pública—, y en un emisor que ya está en producción eso
        significa emitir con una numeración que no le pertenece: la DIAN lo
        rechaza, y los consecutivos que se gasten por el camino no se recuperan.
        """
        emisor = self._emisor_del_cuerpo(request)
        exigir_alcance(request, emisor)

        if emisor.ambiente_facturacion != Ambiente.PRUEBAS:
            raise ErrorSolicitud(
                f"El emisor {emisor.razon_social} ya está en producción para "
                "facturación. La habilitación escribe la resolución del Set de "
                "Pruebas de la DIAN, que no es suya; póngalo en ambiente de "
                "pruebas si de verdad quiere rehabilitarlo."
            )

        certificado = Certificado.objects.filter(emisor=emisor, activo=True).first()
        if certificado is None:
            raise ErrorSolicitud(
                "El emisor no tiene un certificado digital activo. Cárguelo en "
                "/api/emisores/certificado/cargar/ antes de habilitarlo."
            )
        hoy = timezone.localdate()
        if certificado.vigente_hasta and certificado.vigente_hasta < hoy:
            raise ErrorSolicitud(
                f"El certificado digital del emisor venció el "
                f"{certificado.vigente_hasta}. Cargue uno vigente antes de "
                "habilitarlo."
            )

        identificador = request.data.get("identificador")
        ya_registrado = models.SoftwareDian.objects.filter(
            emisor=emisor, identificador=identificador
        ).exists()

        software = None
        if not ya_registrado:
            software = serializers.SoftwareDianSerializer(data=request.data)
            software.is_valid(raise_exception=True)

        with transaction.atomic():
            if software is not None:
                # Solo se desactivan los del mismo tipo: el emisor puede tener
                # a la vez el software de facturación y el de nómina activos, y
                # registrar uno no debe dejar sin software a la otra operación.
                models.SoftwareDian.objects.filter(
                    emisor=emisor, activo=True,
                    tipo=software.validated_data["tipo"],
                ).update(activo=False)
                software.save(activo=True)

            resolucion, creada = models.Resolucion.objects.update_or_create(
                emisor=emisor,
                tipo_factura=TipoFactura.objects.get(codigo="01"),
                prefijo=self.RESOLUCION_SET_PRUEBAS["prefijo"],
                numero_resolucion=self.RESOLUCION_SET_PRUEBAS["numero_resolucion"],
                defaults={
                    **{
                        k: v for k, v in self.RESOLUCION_SET_PRUEBAS.items()
                        if k not in ("prefijo", "numero_resolucion")
                    },
                    "activa": True,
                },
            )

        # Antes se devolvía `{}`, que no decía si había pasado algo. Ahora se
        # dice qué quedó hecho, que es lo que el portal necesita para saber si
        # falta algún paso.
        return Response(
            {
                "emisor": emisor.id,
                "software_registrado": software is not None,
                "resolucion": resolucion.id,
                "resolucion_creada": creada,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="crear-factura-prueba")
    def crear_factura_prueba(self, request):
        """Crea una factura de prueba y su nota crédito, en borrador.

        ``POST /api/emisores/emisor/crear-factura-prueba/`` con
        ``{"emisor": <id>}``, y opcionalmente ``consecutivo``.

        Solo las crea: no las firma ni las envía. Usa la resolución activa del
        emisor para factura de venta, que es la que deja ``crear-habilitacion``.
        """
        emisor = self._emisor_del_cuerpo(request)
        exigir_alcance(request, emisor)

        consecutivo = request.data.get("consecutivo")
        if consecutivo not in (None, ""):
            try:
                consecutivo = int(consecutivo)
            except (TypeError, ValueError):
                raise ErrorSolicitud("El consecutivo debe ser un número entero.")
            if consecutivo < 1:
                raise ErrorSolicitud("El consecutivo debe ser mayor que cero.")
        else:
            consecutivo = None

        resolucion = models.Resolucion.objects.filter(
            emisor=emisor, activa=True, tipo_factura__codigo="01",
        ).order_by("-fecha_resolucion").first()
        if resolucion is None:
            raise ErrorSolicitud(
                "El emisor no tiene una resolución activa de facturación: "
                "ejecute antes crear-habilitacion."
            )

        factura, nota_credito = crear_factura_prueba(
            emisor, resolucion, consecutivo=consecutivo,
        )
        return Response(
            {
                "factura": {
                    "id": str(factura.id),
                    "numero": factura.numero,
                    "estado": factura.estado.nombre,
                },
                "nota_credito": {
                    "id": str(nota_credito.id),
                    "numero": nota_credito.numero,
                    "estado": nota_credito.estado.nombre,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="crear-nomina-prueba")
    def crear_nomina_prueba(self, request):
        """Crea una nómina de prueba en borrador para el emisor.

        ``POST /api/emisores/emisor/crear-nomina-prueba/`` con
        ``{"emisor": <id>}``, y opcionalmente ``prefijo``, ``consecutivo`` y el
        periodo de liquidación (``periodo_inicio`` y ``periodo_fin``, en
        formato ``AAAA-MM-DD``).

        El periodo hace falta para llenar el Set de Pruebas: la DIAN rechaza con
        la regla 90 una segunda nómina del mismo trabajador para el mismo
        periodo. Sin fechas sale el mes en curso.

        Solo la crea: no la firma ni la envía. Para eso están las acciones
        ``emitir`` y ``enviar`` de ``/api/nomina/nomina/{id}/``.
        """
        emisor = self._emisor_del_cuerpo(request)
        exigir_alcance(request, emisor)

        consecutivo = request.data.get("consecutivo")
        if consecutivo not in (None, ""):
            try:
                consecutivo = int(consecutivo)
            except (TypeError, ValueError):
                raise ErrorSolicitud("El consecutivo debe ser un número entero.")
        else:
            consecutivo = None

        def fecha(campo):
            valor = request.data.get(campo)
            if valor in (None, ""):
                return None
            try:
                return date.fromisoformat(valor)
            except (TypeError, ValueError):
                raise ErrorSolicitud(
                    f"'{campo}' debe ser una fecha con formato AAAA-MM-DD."
                )

        try:
            nomina = crear_nomina_prueba(
                emisor,
                prefijo=request.data.get("prefijo") or None,
                consecutivo=consecutivo,
                periodo_inicio=fecha("periodo_inicio"),
                periodo_fin=fecha("periodo_fin"),
            )
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))
        return Response(
            {
                "id": str(nomina.id),
                "numero": nomina.numero,
                "estado": nomina.estado.nombre,
                "empleado": nomina.empleado_id,
                "periodo": [
                    str(nomina.fecha_liquidacion_inicio),
                    str(nomina.fecha_liquidacion_fin),
                ],
                "total_comprobante": str(nomina.total_comprobante),
            },
            status=status.HTTP_201_CREATED,
        )