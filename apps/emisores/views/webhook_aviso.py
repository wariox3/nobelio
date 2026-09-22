"""API de los avisos enviados a los webhooks."""
from rest_framework import viewsets

from apps.emisores import serializers
from apps.emisores.models import WebhookAviso
from apps.nucleo.api import entero_de_query, uuid_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class WebhookAvisoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los avisos mandados a los webhooks del emisor, con lo que respondieron.

    Uno por webhook y por cambio: validarse el documento o notificarse. Se
    mandan una sola vez, sin reintentos; aquí se ve cuáles no llegaron y por
    qué (`codigo_http` y `error`). De solo lectura: los escribe el sistema.

    Filtros: ``?webhook=<id>``, ``?documento=<uuid>``,
    ``?tipo=validacion|notificacion`` y ``?estado=pendiente|entregado|fallido``.
    """

    serializer_class = serializers.WebhookAvisoSerializer
    queryset = WebhookAviso.objects.all()
    campo_emisor = "webhook__emisor"

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if webhook := entero_de_query(params, "webhook"):
            qs = qs.filter(webhook=webhook)
        if documento := uuid_de_query(params, "documento"):
            qs = qs.filter(documento=documento)
        for campo in ("tipo", "estado"):
            if valor := params.get(campo):
                qs = qs.filter(**{campo: valor})
        return qs
