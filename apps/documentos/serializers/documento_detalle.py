"""Serializers de detalles de documento e impuestos por detalle."""
from rest_framework import serializers

from apps.documentos.models import DocumentoDetalleImpuesto, DocumentoDetalle


class DocumentoDetalleImpuestoSerializer(serializers.ModelSerializer):
    tributo_codigo = serializers.CharField(source="tributo.codigo", read_only=True)

    class Meta:
        model = DocumentoDetalleImpuesto
        fields = ["id", "tributo", "tributo_codigo", "base_gravable", "tarifa", "valor"]


class DocumentoDetalleSerializer(serializers.ModelSerializer):
    impuestos = DocumentoDetalleImpuestoSerializer(many=True)

    class Meta:
        model = DocumentoDetalle
        fields = [
            "id", "numero_linea", "descripcion", "codigo_producto",
            "cantidad", "unidad_medida", "valor_unitario", "valor_total",
            "descuento", "impuestos",
            # Datos de negocio: opcionales, viajan al XML si vienen.
            "nota", "marca", "modelo", "centro_costo",
            "periodo_desde", "periodo_hasta",
        ]
