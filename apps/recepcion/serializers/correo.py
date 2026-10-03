"""Serializer de los correos recibidos."""
from rest_framework import serializers

from apps.recepcion.models import Correo

from .documento import DocumentoResumenSerializer


class CorreoSerializer(serializers.ModelSerializer):
    # Solo los del alcance de quien consulta: los acota el prefetch de la vista.
    documentos = DocumentoResumenSerializer(many=True, read_only=True)

    # `sha256` y `raw_key` se quedan fuera: son la idempotencia del endpoint y
    # la clave interna en R2, no información para quien consulta.
    class Meta:
        model = Correo
        fields = [
            "id", "emisor", "alias", "envelope_from", "envelope_to",
            "message_id", "asunto", "recibido_en", "estado", "error_detalle",
            "intentos", "confirmacion_reenvio", "documentos",
        ]
        read_only_fields = fields
