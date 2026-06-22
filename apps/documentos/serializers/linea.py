"""Serializers de líneas de documento e impuestos por línea."""
from rest_framework import serializers

from apps.documentos.models import ImpuestoLinea, LineaDocumento


class ImpuestoLineaSerializer(serializers.ModelSerializer):
    tributo_codigo = serializers.CharField(source="tributo.codigo", read_only=True)

    class Meta:
        model = ImpuestoLinea
        fields = ["id", "tributo", "tributo_codigo", "base_gravable", "tarifa", "valor"]


class LineaDocumentoSerializer(serializers.ModelSerializer):
    impuestos = ImpuestoLineaSerializer(many=True)

    class Meta:
        model = LineaDocumento
        fields = [
            "id", "numero_linea", "descripcion", "codigo_producto",
            "cantidad", "unidad_medida", "valor_unitario", "valor_total",
            "descuento", "impuestos",
        ]
