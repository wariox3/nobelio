"""Serializer de los avisos enviados a los webhooks."""
from rest_framework import serializers

from apps.emisores.models import WebhookAviso


class WebhookAvisoSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookAviso
        fields = [
            "id", "webhook", "documento", "tipo", "estado", "codigo_http",
            "error", "cuerpo", "creado_en", "enviado_en",
        ]
        read_only_fields = fields
