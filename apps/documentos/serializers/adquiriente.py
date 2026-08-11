"""Serializer del adquiriente: va anidado dentro del documento."""
from rest_framework import serializers

from apps.documentos.models import Adquiriente


class AdquirienteSerializer(serializers.ModelSerializer):
    """Datos del receptor. No tiene endpoint propio: se piden en el documento."""

    class Meta:
        model = Adquiriente
        fields = [
            "razon_social", "tipo_identificacion", "numero_identificacion",
            "digito_verificacion", "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "telefono", "correo",
        ]
