"""Serializers de detalles de documento e impuestos por detalle."""
from rest_framework import serializers

from apps.documentos.models import DocumentoDetalleImpuesto, DocumentoDetalle
from apps.nucleo.serializers import EstructuraEstricta


class DocumentoDetalleImpuestoSerializer(EstructuraEstricta, serializers.ModelSerializer):
    tributo_codigo = serializers.CharField(source="tributo.codigo", read_only=True)

    class Meta:
        model = DocumentoDetalleImpuesto
        fields = ["id", "tributo", "tributo_codigo", "base_gravable", "tarifa", "valor"]
        # El modelo los deja en cero por defecto, y así un importe que no venía
        # se guardaba como un cero que nadie había informado. Se exigen en la
        # petición: cero es un valor que se manda, no lo que queda si se olvida.
        extra_kwargs = {
            campo: {"required": True} for campo in ("base_gravable", "tarifa", "valor")
        }


class DocumentoDetalleSerializer(EstructuraEstricta, serializers.ModelSerializer):
    impuestos = DocumentoDetalleImpuestoSerializer(many=True)

    class Meta:
        model = DocumentoDetalle
        fields = [
            "id", "numero_linea", "descripcion", "codigo_producto",
            "cantidad", "unidad_medida", "valor_unitario", "valor_total",
            "descuento", "descuento_motivo", "impuestos",
            # Datos de negocio: opcionales, viajan al XML si vienen.
            "nota", "marca", "modelo", "centro_costo",
            "periodo_desde", "periodo_hasta",
            "periodo_descripcion", "periodo_descripcion_codigo",
        ]
        # Como en el impuesto: los importes de la línea no se pueden omitir. El
        # `descuento` sí, porque una línea sin descuento es lo normal.
        extra_kwargs = {
            campo: {"required": True}
            for campo in ("cantidad", "valor_unitario", "valor_total")
        }
