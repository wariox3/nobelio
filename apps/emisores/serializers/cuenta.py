"""Serializer de la cuenta (agrupador de emisores)."""
from rest_framework import serializers

from apps.emisores.models import Cuenta


class CuentaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cuenta
        fields = ["id", "nombre"]
