"""Serializer de la cuenta (cliente/tenant)."""
from rest_framework import serializers

from apps.cuentas.models import Cuenta


class CuentaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cuenta
        fields = [
            "id", "nombre", "identificacion", "correo_contacto",
            "activa", "creado_en",
        ]
        read_only_fields = ["id", "creado_en"]
