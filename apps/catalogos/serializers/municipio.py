"""Serializer del catálogo municipio."""
from rest_framework import serializers

from apps.catalogos.models import Municipio


class MunicipioSerializer(serializers.ModelSerializer):
    departamento_codigo = serializers.CharField(
        source="departamento.codigo", read_only=True, default=""
    )

    class Meta:
        model = Municipio
        fields = ["id", "codigo", "nombre", "departamento", "departamento_codigo", "activo"]
