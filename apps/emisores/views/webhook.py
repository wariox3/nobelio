"""API de los webhooks del emisor."""
from rest_framework import viewsets

from apps.emisores import models, serializers
from apps.nucleo.api import entero_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class WebhookViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """Los webhooks del emisor: a qué URL avisarle, y de qué.

    Un emisor puede tener varios. `estado_validado` y `estado_notificado` dicen
    si a ese webhook se le avisa de la validación de la DIAN y de la
    notificación al adquiriente. El aviso sale firmado con el `secreto` del
    webhook, una sola vez y sin reintentos; lo que respondió cada uno está en
    ``/api/emisores/webhook-aviso/``. Sin secreto, o si el emisor no tiene
    `referencia_externa`, no se manda: el aviso queda fallido con el motivo.

    Filtro: ``?emisor=<id>``.
    """

    serializer_class = serializers.WebhookSerializer
    queryset = models.Webhook.objects.select_related("emisor")

    def get_queryset(self):
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        return qs.filter(emisor=emisor) if emisor else qs
