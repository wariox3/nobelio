"""Serializer común para los catálogos simples."""
from rest_framework import serializers


class ElementoCatalogoSerializer(serializers.Serializer):
    """Serializer común para los catálogos simples (modelo base abstracto)."""

    id = serializers.IntegerField(read_only=True)
    codigo = serializers.CharField()
    nombre = serializers.CharField()
    activo = serializers.BooleanField()
