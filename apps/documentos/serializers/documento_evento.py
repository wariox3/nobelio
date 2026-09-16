"""Serializer de los eventos de un documento."""
from rest_framework import serializers

from apps.documentos.models import DocumentoEvento


class DocumentoEventoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentoEvento
        fields = ["id", "documento", "tipo", "datos", "fecha"]
        read_only_fields = fields
