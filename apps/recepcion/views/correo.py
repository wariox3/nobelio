"""API de los correos recibidos en el buzón de recepción."""
from rest_framework import filters, viewsets

from apps.nucleo.api import entero_de_query, fecha_de_query
from apps.recepcion import serializers
from apps.recepcion.models import Correo
from apps.seguridad.alcance import AlcanceEmisorMixin


class CorreoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los correos que llegaron a ``<nit>@recepcion.rededoc.co``.

    De solo lectura: los registra el Email Worker de Cloudflare en
    ``POST /recepcion/inbound``. Un correo cuyo alias no es el NIT de ningún
    emisor no tiene emisor, así que fuera del staff no lo ve nadie.

    Filtros: ``?emisor=<id>``, ``?estado=pendiente|procesado|...`` y
    ``?desde=AAAA-MM-DD`` / ``?hasta=AAAA-MM-DD`` sobre ``recibido_en``, ambos
    inclusive. ``?search=`` busca en remitente, asunto y Message-ID, y
    ``?ordering=`` ordena por ``recibido_en`` o ``estado``.
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
