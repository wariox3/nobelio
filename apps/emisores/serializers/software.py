"""Serializer del software DIAN del emisor."""
from rest_framework import serializers

from apps.emisores.models import SoftwareDian


class SoftwareDianSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftwareDian
        fields = [
            "id", "emisor", "identificador", "pin", "id_proveedor",
            "test_set_id", "set_pruebas_aceptado", "activo",
        ]
        extra_kwargs = {
            # El PIN es sensible: se acepta al crear/editar pero nunca se devuelve.
            "pin": {"write_only": True},
        }
