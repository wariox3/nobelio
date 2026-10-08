"""API de los correos recibidos en el buzón de recepción."""
import logging

from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import filters, mixins, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.nucleo.api import ErrorSolicitud, entero_de_query, fecha_de_query
from apps.nucleo.esquema import ErrorSerializer
from apps.nucleo.registro import campos
from apps.recepcion import adjuntos, r2, serializers
from apps.recepcion.models import Correo, Documento
from apps.recepcion.views.adjunto import adjuntos_visibles
from apps.seguridad.alcance import AlcanceEmisorMixin, AlcanceTotal, emisores_permitidos

logger = logging.getLogger(__name__)


class R2NoDisponible(APIException):
    status_code = 503
    default_detail = (
        "No se pueden eliminar correos: el almacenamiento R2 no está configurado."
    )
    default_code = "r2_no_configurado"


class CorreoViewSet(
    AlcanceEmisorMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Los correos que llegaron a ``<nit>@recepcion.rededoc.co``.

    Los registra el Email Worker de Cloudflare en ``POST /recepcion/inbound``.
    Un correo cuyo alias no es el NIT de ningún emisor no tiene emisor, así que
    fuera del staff (o de una llave de alcance global) no lo ve nadie.

    También lista las cargas manuales (``origen = carga``), las de
    ``POST /api/recepcion/documento/cargar/``.

    Filtros: ``?emisor=<id>``, ``?estado=pendiente|procesado|...``,
    ``?origen=correo|carga`` y
    ``?desde=AAAA-MM-DD`` / ``?hasta=AAAA-MM-DD`` sobre ``recibido_en``, ambos
    inclusive. ``?search=`` busca en remitente, asunto y Message-ID, y
    ``?ordering=`` ordena por ``recibido_en`` o ``estado``.

    ``DELETE`` elimina un correo **sin emisor** (el de una empresa que no está
    ni va a estar en la plataforma), junto con su MIME en R2.
    ``DELETE eliminar-admin/`` elimina cualquiera, con sus documentos y todos
    sus archivos; solo el staff o una llave de alcance global.
    """

    serializer_class = serializers.CorreoSerializer
    queryset = Correo.objects.all()

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["envelope_from", "asunto", "message_id"]
    ordering_fields = ["recibido_en", "estado"]

    def get_queryset(self):
        """El filtro por ``emisor`` acota dentro del alcance, nunca lo amplía:
        el mixin ya restringió el queryset antes de llegar aquí."""
        qs = super().get_queryset()
        # Un correo puede traer documentos de otro emisor (manda el NIT receptor
        # del XML): dentro del correo solo van los que alcanza quien consulta.
        documentos = Documento.objects.select_related("documento_tipo")
        permitidos = emisores_permitidos(self.request)
        if permitidos is not None:
            documentos = documentos.filter(emisor__in=permitidos)
        qs = qs.prefetch_related(Prefetch("documentos", queryset=documentos))
        params = self.request.query_params
        if (emisor := entero_de_query(params, "emisor")) is not None:
            qs = qs.filter(emisor=emisor)
        if estado := params.get("estado"):
            qs = qs.filter(estado=estado)
        if origen := params.get("origen"):
            qs = qs.filter(origen=origen)
        if desde := fecha_de_query(params, "desde"):
            qs = qs.filter(recibido_en__date__gte=desde)
        if hasta := fecha_de_query(params, "hasta"):
            qs = qs.filter(recibido_en__date__lte=hasta)
        return qs

    def destroy(self, request, *args, **kwargs):
        """Borra el correo sin emisor, sus adjuntos en B2 y su MIME en R2.

        Solo los correos sin emisor: los de un emisor son información fiscal
        suya (para esos está ``eliminar-admin/``). Un correo sin emisor nunca
        tiene documentos, pero sí puede tener adjuntos.

        Si B2 o R2 fallan, las filas vuelven y responde 502 (lo traduce el
        ``exception_handler``); repetir termina el trabajo.
        """
        correo = self.get_object()
        if correo.emisor_id is not None:
            raise ErrorSolicitud(
                "Solo se eliminan correos sin emisor; este pertenece a un emisor."
            )
        _, archivos = _eliminar(correo)
        logger.info("recepcion.correo_eliminado %s", campos(
            correo=kwargs["pk"], alias=correo.alias, adjuntos=archivos,
            raw_key=correo.raw_key, por=request.user,
        ))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer("CorreoEliminadoAdmin", {
                "correo": drf_serializers.IntegerField(),
                "documentos": drf_serializers.IntegerField(),
                "archivos": drf_serializers.IntegerField(),
            }),
            403: ErrorSerializer, 502: ErrorSerializer, 503: ErrorSerializer,
        },
    )
    @action(
        detail=True, methods=["delete"], url_path="eliminar-admin",
        permission_classes=[IsAuthenticated, AlcanceTotal],
    )
    def eliminar_admin(self, request, pk=None):
        """Elimina el correo aunque tenga emisor, con todo lo que cuelga de él.

        Borra los documentos que salieron del correo (de cualquier emisor), todos
        sus adjuntos en B2 y el MIME en R2. Solo el staff o una llave de alcance
        global: son documentos fiscales, y el ERP puede haberlos procesado ya.

        Si B2 o R2 fallan, las filas vuelven y responde 502; repetir la petición
        termina el trabajo (ver ``adjuntos.eliminar_correo``).
        """
        correo = self.get_object()
        documentos, archivos = _eliminar(correo)
        logger.info("recepcion.correo_eliminado_admin %s", campos(
            correo=pk, alias=correo.alias, emisor=correo.emisor_id,
            documentos=documentos, archivos=archivos,
            raw_key=correo.raw_key, por=request.user,
        ))
        return Response({"correo": int(pk), "documentos": documentos, "archivos": archivos})

    @extend_schema(responses=serializers.AdjuntoSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="adjuntos", pagination_class=None)
    def adjuntos(self, request, pk=None):
        """Todos los archivos que trajo el correo, ya fuera de sus ZIP.

        Los de un documento de otro emisor (el NIT receptor del XML manda) solo
        los ve quien alcanza ese emisor. Se bajan con
        ``/api/recepcion/adjunto/<id>/descargar/``.
        """
        correo = self.get_object()
        visibles = adjuntos_visibles(request).filter(correo=correo)
        return Response(serializers.AdjuntoSerializer(visibles, many=True).data)


def _eliminar(correo):
    """``adjuntos.eliminar_correo``, con el 503 si falta R2."""
    try:
        return adjuntos.eliminar_correo(correo)
    except r2.R2NoConfigurado:
        logger.error("recepcion.r2_no_configurado R2_* no está configurado")
        raise R2NoDisponible
