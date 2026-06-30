"""Serializer de los errores/notificaciones DIAN de un documento."""
from rest_framework import serializers

from apps.documentos.models import DocumentoError


class DocumentoErrorSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source="get_tipo_display", read_only=True)

    class Meta:
        model = DocumentoError
        fields = ["id", "regla", "tipo", "tipo_display", "mensaje"]
