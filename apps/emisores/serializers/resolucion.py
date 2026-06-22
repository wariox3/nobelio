"""Serializer de resoluciones de facturación."""
from rest_framework import serializers

from apps.emisores.models import ResolucionFacturacion


class ResolucionFacturacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResolucionFacturacion
        fields = [
            "id", "emisor", "tipo_factura", "numero_resolucion", "fecha_resolucion",
            "prefijo", "rango_desde", "rango_hasta", "vigente_desde", "vigente_hasta",
            "consecutivo_actual", "activa",
        ]
        # La clave técnica es sensible: no se expone en la API.
