"""Serializers de catálogos DIAN."""
from rest_framework import serializers

from . import models


class ElementoCatalogoSerializer(serializers.Serializer):
    """Serializer común para los catálogos simples (modelo base abstracto)."""

    id = serializers.IntegerField(read_only=True)
    codigo = serializers.CharField()
    nombre = serializers.CharField()
    activo = serializers.BooleanField()


class MunicipioSerializer(serializers.ModelSerializer):
    departamento_codigo = serializers.CharField(
        source="departamento.codigo", read_only=True, default=""
    )

    class Meta:
        model = models.Municipio
        fields = ["id", "codigo", "nombre", "departamento", "departamento_codigo", "activo"]
