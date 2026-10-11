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
        """Permite filtrar por emisor: ``/api/emisores/resolucion/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        return qs.filter(emisor=emisor) if emisor else qs

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

    @action(detail=False, methods=["get"], url_path="consulta-dian")
    def consulta_dian(self, request):
        """Consulta en la DIAN los rangos de numeración de un software (GetNumberingRange).

        ``GET /api/emisores/resolucion/consulta-dian/?software=<id>``

        La DIAN asocia los prefijos al software, no al emisor: cada software
        (facturación, documento equivalente) tiene los suyos, así que se indica
        cuál consultar. El emisor sale de él, y el ambiente también: producción
        si el emisor ya está habilitado para esa operación y habilitación si
        no. En los entornos ``dev`` y ``test`` se consulta siempre en
        habilitación.

        Solo para previsualizar: devuelve los rangos autorizados sin persistir.
        La clave técnica no se expone (es sensible); se indica únicamente si
        está presente. Para guardarla usa ``importar-dian``.
        """
        software, _ = self._software_a_consultar(
            entero_de_query(request.query_params, "software")
        )
        respuesta = self._consultar(software)
        datos = [
            {
                "prefijo": r.prefijo,
                "numero_resolucion": r.numero_resolucion,
                "fecha_resolucion": _a_fecha(r.fecha_resolucion),
                "rango_desde": r.rango_desde,
                "rango_hasta": r.rango_hasta,
                "vigente_desde": _a_fecha(r.vigente_desde),
                "vigente_hasta": _a_fecha(r.vigente_hasta),
                "tiene_clave_tecnica": bool(r.clave_tecnica),
            }
            for r in respuesta.rangos
        ]
        # Incluimos el mensaje de la DIAN: si rangos viene vacío explica el motivo
        # (p. ej. "No registra prefijos asociados al código de software: ...").
        return Response({
            "codigo": respuesta.codigo,
            "descripcion": respuesta.descripcion,
            "rangos": datos,
        })

    @extend_schema(
        request=inline_serializer(
            "ImportarResolucionesSolicitud",
            {"emisor": drf_serializers.IntegerField()},
        ),
        responses=inline_serializer(
            "ImportarResolucionesRespuesta",
            {"creadas": drf_serializers.IntegerField()},
        ),
    )
    @action(detail=False, methods=["post"], url_path="importar-dian")
    def importar_dian(self, request):
        """Importa de la DIAN los rangos y crea las resoluciones que falten.

        ``POST /api/emisores/resolucion/importar-dian/`` con ``{"emisor": <id>}``.

        La DIAN solo responde por software (GetNumberingRange no trae las de
        todo el emisor), así que se recorren los softwares del emisor con el
        Set de Pruebas aceptado —facturación y documento equivalente; el de
        nómina no se numera con resolución— y se importan las de todos. Sin
        ninguno aceptado responde 400 (``software_no_aceptado``).

        Crea solo las resoluciones que el emisor todavía no tiene (se comparan
        prefijo + número de resolución), guardando la ``clave_tecnica`` en el
        servidor. Las que ya existen no se tocan: si la DIAN cambió un rango se
        edita a mano.

        Responde 200 con ``{"creadas": <n>}``: confirma que el proceso corrió y
        cuántas resoluciones nuevas dejó. No las devuelve; se leen con el
        listado, ``GET /api/emisores/resolucion/?emisor=<id>``.

        Si la DIAN no devuelve rangos para ninguno de los softwares consultados
        responde 400 con su mensaje; si los devuelve para alguno, se importa lo
        de ese.

        **El tipo de factura lo decide el software**: lo importado con el de
        facturación entra como factura de venta (``01``) y lo del documento
        equivalente, como P.O.S. (``20``).
        """
        softwares = self._softwares_a_importar(request.data.get("emisor"))

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

        # Solo se confirma que el proceso corrió: las resoluciones se leen con
        # el listado (`GET /api/emisores/resolucion/?emisor=<id>`).
        return Response({"creadas": creadas})

    @extend_schema(
        request=serializers.ValidarResolucionSerializer,
        responses=serializers.ResolucionSerializer(many=True),
    )
    @action(detail=False, methods=["post"], url_path="validar")
    def validar(self, request):
        """Comprueba en la DIAN que una resolución está asociada al emisor.

        ``POST /api/emisores/resolucion/validar/`` con ``{"emisor": <id>,
        "tipo_software": "facturacion", "numero_resolucion": "18764..."}``.

        Busca el software de ese tipo que el emisor tiene con el Set de Pruebas
        aceptado; si no lo hay responde 400 (``software_no_aceptado``). Con él
        hace lo mismo que ``importar-dian`` —consulta GetNumberingRange y crea
        las resoluciones que falten, con su ``clave_tecnica``— y busca el
        número entre lo que devolvió la DIAN.

        Si está, responde 200 con las resoluciones de ese número, ya guardadas:
        es una lista porque la DIAN puede autorizar varios prefijos con una
        misma resolución. Si no está, 400 (``resolucion_no_asociada``).
        """
        entrada = serializers.ValidarResolucionSerializer(
            data=request.data, context=self.get_serializer_context(),
        )
        entrada.is_valid(raise_exception=True)
        emisor = entrada.validated_data["emisor"]
        tipo = entrada.validated_data["tipo_software"]
        numero = entrada.validated_data["numero_resolucion"].strip()

        software = models.SoftwareDian.objects.filter(
            emisor=emisor, tipo=tipo, set_pruebas_aceptado=True,
        ).select_related("emisor").first()
        if software is None:
            nombre = models.SoftwareDian.Tipo(tipo).label.lower()
            raise ErrorSolicitud(
                f"El emisor no tiene un software de {nombre} aceptado por la "
                f"DIAN.",
                code="software_no_aceptado",
            )
        codigo_tipo_factura = self._codigo_tipo_factura(software)
        tipo_factura = get_object_or_404(TipoFactura, codigo=codigo_tipo_factura)

        respuesta = self._consultar(software)
        prefijos = [
            r.prefijo or "" for r in respuesta.rangos
            if r.numero_resolucion == numero
        ]
        if not prefijos:
            raise ErrorSolicitud(
                f"La resolución {numero} no está asociada en la DIAN al "
                f"software de {software.get_tipo_display().lower()} del emisor.",
                code="resolucion_no_asociada",
            )

        self._crear_las_que_falten(software, tipo_factura, respuesta)
        resoluciones = self.get_queryset().filter(
            emisor=emisor, numero_resolucion=numero, prefijo__in=prefijos,
        ).order_by("prefijo")
        return Response(self.get_serializer(resoluciones, many=True).data)

    # -- Internos -----------------------------------------------------------

    def _software_a_consultar(self, valor):
        """El software indicado y el código de tipo de factura de sus rangos.

        Rechaza el de nómina: esa operación no se numera con resolución, así
        que no tiene rangos que consultar.
        """
        if valor in (None, ""):
            raise ValidationError(
                {"software": "El parámetro 'software' es obligatorio."}
            )
        try:
            pk = int(valor)
        except (TypeError, ValueError):
            raise ValidationError({"software": "Debe ser un número entero."})
        software = (
            models.SoftwareDian.objects.select_related("emisor")
            .filter(pk=pk).first()
        )
        if software is None:
            raise NotFound(f"No existe un software con id {pk}.")
        exigir_alcance(self.request, software.emisor)
        return software, self._codigo_tipo_factura(software)

    def _softwares_a_importar(self, valor):
        """Los softwares de ``importar-dian``, cada uno con su código de tipo.

        Los del emisor indicado que tienen el Set de Pruebas aceptado y se
        numeran con resolución.
        """
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
        softwares = list(
            models.SoftwareDian.objects
            .filter(
                emisor=emisor, set_pruebas_aceptado=True,
                tipo__in=RESOLUCION_POR_TIPO_DE_SOFTWARE,
            )
            .select_related("emisor")
            .order_by("tipo")
        )
        if not softwares:
            raise ErrorSolicitud(
                "El emisor no tiene ningún software aceptado por la DIAN que "
                "se numere con resolución.",
                code="software_no_aceptado",
            )
        return [(s, self._codigo_tipo_factura(s)) for s in softwares]

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
            )
            for r in nuevos
        ]

    def _consultar(self, software):
        """Llama a la DIAN; devuelve un ``RespuestaRangos`` o lanza la excepción."""
        emisor = software.emisor
        # Habilitado para esa operación, sus prefijos están en producción;
        # mientras no, en habilitación. En dev y test se consulta siempre ahí.
        ambiente = ambiente_segun_habilitacion(
            emisor.habilitado_para(software.tipo)
        )
        try:
            return dian.consultar_rangos_numeracion(
                emisor, software=software, ambiente=ambiente,
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
