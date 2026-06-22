"""Serializer del adquirente."""
from rest_framework import serializers

from apps.documentos.models import Adquirente


class AdquirenteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Adquirente
        fields = [
            "id", "razon_social", "tipo_identificacion", "numero_identificacion",
            "digito_verificacion", "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "telefono", "correo",
        ]
