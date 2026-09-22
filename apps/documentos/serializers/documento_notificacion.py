"""Serializer de las notificaciones de un documento."""
from rest_framework import serializers

from apps.documentos.models import DocumentoNotificacion


class DocumentoNotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentoNotificacion
        fields = [
            "id", "documento", "estado", "destinatario", "copia", "archivo",
            "tamano", "contenido", "codigo_envio", "error", "fecha",
        ]
        read_only_fields = fields
