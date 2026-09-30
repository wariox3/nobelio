"""Serializer del catálogo municipio."""
from rest_framework import serializers

from apps.catalogos.models import Municipio


class MunicipioSerializer(serializers.ModelSerializer):
    departamento_codigo = serializers.CharField(
        source="departamento.codigo", read_only=True, default=""
    )

    class Meta:
        model = Municipio
        fields = [
            "id", "codigo", "nombre", "codigo_postal",
            "departamento", "departamento_codigo", "activo",
        ]
        # El id ya no es autoincremental (es fijo por código), así que
        # ModelSerializer lo daría por escribible.
        read_only_fields = ["id"]
