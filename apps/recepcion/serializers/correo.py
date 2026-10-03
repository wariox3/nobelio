"""Serializer de los correos recibidos."""
from rest_framework import serializers

from apps.recepcion.models import Correo

from .documento import DocumentoRecibidoResumenSerializer


class CorreoSerializer(serializers.ModelSerializer):
    # Solo los del alcance de quien consulta: los acota el prefetch de la vista.
    documentos = DocumentoRecibidoResumenSerializer(many=True, read_only=True)

    # `sha256` se queda fuera: es la idempotencia del endpoint, no información
    # para quien consulta. `raw_key` sí va: es la clave del MIME en R2, útil
    # para ubicar el correo original; sin las credenciales de R2 no da acceso.
    class Meta:
        model = Correo
        fields = [
            "id", "emisor", "alias", "raw_key", "envelope_from", "envelope_to",
            "message_id", "asunto", "recibido_en", "estado", "error_detalle",
            "intentos", "confirmacion_reenvio", "documentos",
        ]
        read_only_fields = fields
