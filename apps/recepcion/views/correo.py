"""API de los correos recibidos en el buzón de recepción."""
import logging

from django.db import transaction
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
from apps.recepcion import r2, serializers
from apps.recepcion.models import Correo, Documento
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

    Filtros: ``?emisor=<id>``, ``?estado=pendiente|procesado|...`` y
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
        if desde := fecha_de_query(params, "desde"):
            qs = qs.filter(recibido_en__date__gte=desde)
        if hasta := fecha_de_query(params, "hasta"):
            qs = qs.filter(recibido_en__date__lte=hasta)
        return qs

    def destroy(self, request, *args, **kwargs):
        """Borra la fila y el MIME en R2, o ninguno de los dos.

        Solo los correos sin emisor: los de un emisor son información fiscal
        suya, y de ellos colgarán sus documentos.

        R2 se borra dentro de la transacción, después de la fila: si R2 falla,
        la fila vuelve y el correo sigue completo (el error de botocore lo
        traduce a 502 el ``exception_handler``). Borrar una clave inexistente
        no es error en R2, así que reintentar es seguro.
        """
        correo = self.get_object()
        if correo.emisor_id is not None:
            raise ErrorSolicitud(
                "Solo se eliminan correos sin emisor; este pertenece a un emisor."
            )
        try:
            with transaction.atomic():
                correo.delete()
                r2.borrar_mime(correo.raw_key)
        except r2.R2NoConfigurado:
            logger.error("recepcion.r2_no_configurado R2_* no está configurado")
            raise R2NoDisponible
        logger.info("recepcion.correo_eliminado %s", campos(
            correo=kwargs["pk"], alias=correo.alias, raw_key=correo.raw_key,
            por=request.user,
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

        Borra los documentos que salieron del correo (de cualquier emisor), sus
        archivos en B2 y el MIME en R2. Solo el staff o una llave de alcance
        global: son documentos fiscales, y el ERP puede haberlos procesado ya.

        Las filas se borran en una transacción y los archivos dentro de ella,
        después: si B2 o R2 fallan, las filas vuelven y responde 502. Los
        archivos que ya se borraron no vuelven, pero repetir la petición
        termina el trabajo, porque borrar lo que ya no existe no es error. Así
        nunca quedan archivos sin dueño en los buckets.
        """
        correo = self.get_object()
        if not r2.r2_habilitado():
            # Antes de tocar nada: sin R2 el MIME quedaría huérfano.
            logger.error("recepcion.r2_no_configurado R2_* no está configurado")
            raise R2NoDisponible
        with transaction.atomic():
            correo = Correo.objects.select_for_update().get(pk=correo.pk)
            documentos = list(Documento.objects.filter(correo=correo))
            archivos = [
                campo
                for documento in documentos
                for campo in (
                    documento.xml_archivo, documento.xml_factura_archivo,
                    documento.pdf_archivo,
                )
                if campo
            ]
            Documento.objects.filter(correo=correo).delete()
            correo.delete()
            for archivo in archivos:
                archivo.storage.delete(archivo.name)
            r2.borrar_mime(correo.raw_key)
        logger.info("recepcion.correo_eliminado_admin %s", campos(
            correo=pk, alias=correo.alias, emisor=correo.emisor_id,
            documentos=len(documentos), archivos=len(archivos),
            raw_key=correo.raw_key, por=request.user,
        ))
        return Response({
            "correo": int(pk), "documentos": len(documentos), "archivos": len(archivos),
        })
