"""Serializer del emisor."""
from rest_framework import serializers

from apps.emisores.models import Emisor

from .resolucion import ResolucionFacturacionSerializer


class EmisorSerializer(serializers.ModelSerializer):
    resoluciones = ResolucionFacturacionSerializer(many=True, read_only=True)

    class Meta:
        model = Emisor
        fields = [
            "id", "razon_social", "nombre_comercial",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion",
            "telefono", "correo", "activo", "resoluciones",
        ]
