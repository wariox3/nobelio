"""Serializers de la API de emisores."""
from rest_framework import serializers

from . import models


class ResolucionFacturacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ResolucionFacturacion
        fields = [
            "id", "emisor", "tipo_factura", "numero_resolucion", "fecha_resolucion",
            "prefijo", "rango_desde", "rango_hasta", "vigente_desde", "vigente_hasta",
            "consecutivo_actual", "activa",
        ]
        # La clave técnica es sensible: no se expone en la API.


class EmisorSerializer(serializers.ModelSerializer):
    resoluciones = ResolucionFacturacionSerializer(many=True, read_only=True)

    class Meta:
        model = models.Emisor
        fields = [
            "id", "razon_social", "nombre_comercial",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion",
            "telefono", "correo", "activo", "resoluciones",
        ]
