"""Serializer del adquiriente."""
from rest_framework import serializers

from apps.documentos.models import Adquiriente


class AdquirienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Adquiriente
        fields = [
            "id", "razon_social", "tipo_identificacion", "numero_identificacion",
            "digito_verificacion", "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "telefono", "correo",
        ]
