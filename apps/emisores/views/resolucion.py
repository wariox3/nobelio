"""API de resoluciones de numeración."""
from datetime import date

import requests
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.catalogos.models import TipoFactura
from apps.dian import servicios as dian
from apps.dian import soap
from apps.emisores import models, serializers
from apps.emisores.servicios import crear_documento_de_prueba
from apps.emisores.servicios.resolucion_pruebas import (
    RESOLUCION_POR_TIPO_DE_SOFTWARE,
)
from apps.nucleo.api import ErrorPasarela, ErrorSolicitud, entero_de_query
from apps.nucleo.entorno import ambiente_segun_habilitacion
from apps.seguridad.alcance import AlcanceEmisorMixin, exigir_alcance


# Lo que `validar` compara entre lo que envía quien llama y lo registrado.
CAMPOS_A_CONTRASTAR = ("rango_desde", "rango_hasta", "vigente_desde", "vigente_hasta")


def _a_fecha(valor: str):
    """Convierte una fecha de la DIAN (ISO, con o sin hora) a ``date``."""
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


class ResolucionViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    queryset = models.Resolucion.objects.select_related("emisor", "tipo_factura")
    serializer_class = serializers.ResolucionSerializer

    def get_queryset(self):
        """Permite filtrar por emisor, por ambiente DIAN y por módulo.

        ``/api/emisores/resolucion/?emisor=<id>&ambiente=<1|2>&modulo=<facturacion|nomina|compras>``:
        en ``ambiente``, 1 es producción y 2 habilitación. Los tres son
        opcionales y se combinan.
        """
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        if emisor:
            qs = qs.filter(emisor=emisor)
        ambiente = entero_de_query(self.request.query_params, "ambiente")
        if ambiente:
            qs = qs.filter(ambiente=ambiente)
        modulo = self.request.query_params.get("modulo")
        if modulo:
            if modulo not in models.Resolucion.Modulo.values:
                raise ErrorSolicitud(
                    "El filtro 'modulo' tiene que ser uno de: "
                    + ", ".join(models.Resolucion.Modulo.values)
                    + f"; se recibió '{modulo}'."
                )
            qs = qs.filter(modulo=modulo)
        return qs

    def perform_destroy(self, instance):
        # Comprobado antes de borrar y no capturando el ProtectedError: así el
        # mensaje dice qué hacer, en vez de un 500.
        documentos = instance.documentos.count()
        if documentos:
            raise ErrorSolicitud(
                f"La resolución {instance.numero_resolucion} tiene "
                f"{documentos} documento(s) y no se puede borrar. Para que "
                f"deje de numerar documentos nuevos, desactívela "
                f"(PATCH con \"activa\": false)."
            )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="crear-documento-prueba")
    def crear_documento_prueba(self, request, pk=None):
        """Crea un documento de prueba en borrador sobre esta resolución.

        ``POST /api/emisores/resolucion/{id}/crear-documento-prueba/``, con
        ``{"consecutivo": <n>}`` opcional.

        Es el mismo documento que siembra el alta del software, pero de uno en
        uno y sobre la resolución que se indique. Sirve para completar un Set
        de Pruebas al que le faltan documentos sin tener que componerlos a mano.

        **El tipo lo decide la resolución**, no quien llama: su ``tipo_factura``
        es el mismo código que el ``codigo_dian`` del tipo de documento, así que
        una resolución de facturación (01) da una factura de venta y una de
        documento soporte (05), un documento soporte.

        Sin ``consecutivo`` toma el siguiente libre: el posterior al mayor que
        ya se usó de esa resolución para ese tipo, o el primero del rango si
        todavía no hay ninguno.

        Solo lo crea: no lo firma ni lo envía. Para eso está ``emitir`` de
        ``/api/documentos/documento/{id}/``, que firma y envía.
        """
        # `get_object` va contra el queryset del mixin, así que una resolución
        # fuera del alcance no se encuentra (404) en vez de responder 403 y
        # delatar que existe.
        resolucion = self.get_object()

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
            documento = crear_documento_de_prueba(resolucion, consecutivo)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))

        return Response(
            {
                "id": str(documento.id),
                "documento_tipo": documento.documento_tipo.codigo,
                "numero": documento.numero,
                "consecutivo": documento.consecutivo,
                "estado": documento.estado.nombre,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            "ImportarResolucionesSolicitud",
            {"emisor": drf_serializers.IntegerField()},
        ),
        responses=serializers.ResolucionSerializer(many=True),
    )
    @action(detail=False, methods=["post"], url_path="importar-dian")
    def importar_dian(self, request):
        """Importa de la DIAN los rangos del emisor y devuelve todas sus resoluciones.

        ``POST /api/emisores/resolucion/importar-dian/`` con ``{"emisor": <id>}``.

        La DIAN solo responde por software (GetNumberingRange no trae las de
        todo el emisor), así que se consultan **todos** los softwares del
        emisor que se numeran con resolución —facturación y documento
        equivalente, con el Set de Pruebas aceptado o no; el de nómina no se
        numera con resolución—. Sin ninguno responde 400 (``sin_software``).

        Crea las resoluciones que el emisor todavía no tiene (se comparan
        prefijo + número de resolución), guardando la ``clave_tecnica`` en el
        servidor. Las que ya existen no se tocan: si la DIAN cambió un rango se
        edita a mano.

        Responde 200 con la lista de **todas** las resoluciones del emisor, las
        nuevas y las que ya tenía.

        Si la DIAN no devuelve rangos para ninguno de los softwares consultados
        responde 400 con su mensaje; si los devuelve para alguno, se importa lo
        de ese.

        **El tipo de factura lo decide el software**: lo importado con el de
        facturación entra como factura de venta (``01``) y lo del documento
        equivalente, como P.O.S. (``20``).
        """
        emisor, _ = self._importar(request.data.get("emisor"))
        resoluciones = self.get_queryset().filter(emisor=emisor)
        return Response(self.get_serializer(resoluciones, many=True).data)

    @extend_schema(
        request=serializers.ValidarResolucionesSerializer,
        responses=serializers.ResolucionValidadaSerializer(many=True),
    )
    @action(detail=False, methods=["post"], url_path="validar")
    def validar(self, request):
        """Contrasta una lista de resoluciones con las que el emisor tiene.

        ``POST /api/emisores/resolucion/validar/`` con::

            {"emisor": <id>, "resoluciones": [{
                "numero_resolucion": "18764...", "prefijo": "FE",
                "rango_desde": 990000000, "rango_hasta": 995000000,
                "vigente_desde": "2019-01-19", "vigente_hasta": "2030-01-19"
            }]}

        ``resoluciones`` es opcional: sin ella, o vacía, no hace nada y responde
        200 con una lista vacía, sin consultar a la DIAN.

        Primero corre el mismo proceso que ``importar-dian`` —y responde sus
        mismos errores—, para comparar contra lo que la DIAN tiene hoy. Después
        busca cada resolución de la lista entre las del emisor, por número y,
        si se indica, por prefijo.

        Responde 200 con una entrada por cada resolución recibida, en el mismo
        orden:

        - ``validado``: si el emisor tiene esa resolución.
        - ``inconsistencia``: si la tiene pero alguno de los cuatro datos
          enviados (``rango_desde``, ``rango_hasta``, ``vigente_desde``,
          ``vigente_hasta``) no coincide con el registrado. Solo se comparan
          los que se envían.
        - ``prefijo`` y esos cuatro datos **tal como están registrados**;
          ``null`` si no se tiene.
        """
        entrada = serializers.ValidarResolucionesSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        pedidas = entrada.validated_data["resoluciones"]
        if not pedidas:
            # Sin nada que contrastar no se molesta a la DIAN: solo se comprueba
            # que el emisor exista y esté al alcance.
            self._emisor_indicado(entrada.validated_data["emisor"])
            return Response([])

        emisor, _ = self._importar(entrada.validated_data["emisor"])

        registradas = {}
        for resolucion in models.Resolucion.objects.filter(emisor=emisor):
            registradas.setdefault(resolucion.numero_resolucion, []).append(resolucion)

        resultado = [
            self._validar_una(pedida, registradas)
            for pedida in pedidas
        ]
        return Response(
            serializers.ResolucionValidadaSerializer(resultado, many=True).data
        )

    # -- Internos -----------------------------------------------------------

    def _importar(self, valor):
        """El proceso de ``importar-dian``: el emisor y cuántas resoluciones creó.

        Consulta a la DIAN con cada software del emisor que se numera con
        resolución y crea las resoluciones que le falten.
        """
        emisor = self._emisor_indicado(valor)
        softwares = self._softwares_con_resolucion(emisor)

        # Primero se consulta todo y después se crea: si la DIAN falla con el
        # segundo software, no queda importado a medias lo del primero.
        consultas = [
            (
                software,
                get_object_or_404(TipoFactura, codigo=codigo_tipo_factura),
                self._consultar(software),
            )
            for software, codigo_tipo_factura in softwares
        ]

        # Si la DIAN no devuelve rangos, no hay nada que importar: devolvemos su
        # mensaje (p. ej. software sin prefijos asociados) en lugar de 0 cambios.
        if not any(respuesta.rangos for _, _, respuesta in consultas):
            mensajes = [r.descripcion for _, _, r in consultas if r.descripcion]
            raise ErrorSolicitud(
                " ".join(dict.fromkeys(mensajes))
                or "La DIAN no devolvió rangos de numeración para este emisor."
            )

        creadas = 0
        for software, tipo_factura, respuesta in consultas:
            creadas += len(
                self._crear_las_que_falten(software, tipo_factura, respuesta)
            )

        return emisor, creadas

    def _emisor_indicado(self, valor):
        """El emisor con ese id, si existe y está al alcance del solicitante."""
        if valor in (None, ""):
            raise ValidationError(
                {"emisor": "El parámetro 'emisor' es obligatorio."}
            )
        try:
            pk = int(valor)
        except (TypeError, ValueError):
            raise ValidationError({"emisor": "Debe ser un número entero."})
        emisor = models.Emisor.objects.filter(pk=pk).first()
        if emisor is None:
            raise NotFound(f"No existe un emisor con id {pk}.")
        exigir_alcance(self.request, emisor)
        return emisor

    def _softwares_con_resolucion(self, emisor):
        """Los softwares del emisor que se numeran con resolución (aceptados o
        no), cada uno con su código de tipo de factura."""
        softwares = list(
            models.SoftwareDian.objects
            .filter(emisor=emisor, tipo__in=RESOLUCION_POR_TIPO_DE_SOFTWARE)
            .select_related("emisor")
            .order_by("tipo")
        )
        if not softwares:
            raise ErrorSolicitud(
                "El emisor no tiene ningún software que se numere con "
                "resolución (facturación o documento equivalente).",
                code="sin_software",
            )
        return [(s, self._codigo_tipo_factura(s)) for s in softwares]

    def _validar_una(self, pedida, registradas):
        """El resultado de ``validar`` para una resolución de la lista."""
        candidatas = registradas.get(pedida["numero_resolucion"], [])
        if "prefijo" in pedida:
            candidatas = [r for r in candidatas if r.prefijo == pedida["prefijo"]]

        def difiere(resolucion):
            return any(
                campo in pedida and pedida[campo] != getattr(resolucion, campo)
                for campo in CAMPOS_A_CONTRASTAR
            )

        # Sin prefijo, un número puede tener varios registros (la DIAN autoriza
        # varios prefijos con una resolución): se prefiere el que coincide.
        candidatas = sorted(candidatas, key=lambda r: (difiere(r), r.prefijo))
        resolucion = candidatas[0] if candidatas else None
        return {
            "numero_resolucion": pedida["numero_resolucion"],
            "validado": resolucion is not None,
            "inconsistencia": resolucion is not None and difiere(resolucion),
            "prefijo": resolucion.prefijo if resolucion else pedida.get("prefijo"),
            **{
                campo: resolucion and getattr(resolucion, campo)
                for campo in CAMPOS_A_CONTRASTAR
            },
        }

    def _codigo_tipo_factura(self, software):
        """El código de tipo de factura con el que entran los rangos del software."""
        receta = RESOLUCION_POR_TIPO_DE_SOFTWARE.get(software.tipo)
        if receta is None:
            raise ErrorSolicitud(
                f"El software de {software.get_tipo_display().lower()} no se "
                f"numera con resolución: no tiene rangos que consultar."
            )
        return receta["codigo_tipo_factura"]

    def _crear_las_que_falten(self, software, tipo_factura, respuesta):
        """Crea las resoluciones de ``respuesta`` que el emisor aún no tiene."""
        emisor = software.emisor
        # Se mira el emisor entero, sin filtrar por tipo de factura: una misma
        # resolución no se importa dos veces por estar registrada bajo otro tipo.
        existentes = set(
            models.Resolucion.objects
            .filter(emisor=emisor)
            .values_list("prefijo", "numero_resolucion")
        )
        nuevos = [
            r for r in respuesta.rangos
            if (r.prefijo or "", r.numero_resolucion) not in existentes
        ]
        return [
            models.Resolucion.objects.create(
                emisor=emisor,
                tipo_factura=tipo_factura,
                prefijo=r.prefijo,
                numero_resolucion=r.numero_resolucion,
                fecha_resolucion=_a_fecha(r.fecha_resolucion),
                rango_desde=r.rango_desde,
                rango_hasta=r.rango_hasta,
                vigente_desde=_a_fecha(r.vigente_desde),
                vigente_hasta=_a_fecha(r.vigente_hasta),
                clave_tecnica=r.clave_tecnica,
                activa=True,
                # El mismo con el que se consultó: de ahí salieron estos rangos.
                ambiente=self._ambiente_de(software),
            )
            for r in nuevos
        ]

    def _ambiente_de(self, software):
        """El ambiente DIAN en el que están los rangos de ese software.

        Habilitado para esa operación, sus prefijos están en producción;
        mientras no, en habilitación. En dev y test se consulta siempre ahí.
        """
        return ambiente_segun_habilitacion(
            software.emisor.habilitado_para(software.tipo)
        )

    def _consultar(self, software):
        """Llama a la DIAN; devuelve un ``RespuestaRangos`` o lanza la excepción."""
        try:
            return dian.consultar_rangos_numeracion(
                software.emisor, software=software,
                ambiente=self._ambiente_de(software),
            )
        except dian.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.HTTPError as exc:
            # La DIAN suele responder 500 con un soap:Fault que explica la causa.
            fault = ""
            if exc.response is not None:
                fault = soap.extraer_fault(exc.response.content)
            raise ErrorPasarela(f"La DIAN rechazó la consulta: {fault or exc}")
        except requests.RequestException as exc:
            raise ErrorPasarela(f"Error al comunicarse con la DIAN: {exc}")
