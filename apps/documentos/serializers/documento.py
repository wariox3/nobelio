"""Serializers de lectura y creación del documento electrónico."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.documentos import models

from .documento_detalle import DocumentoDetalleSerializer


class DocumentoSerializer(serializers.ModelSerializer):
    """Serializer de lectura del documento, con detalles anidados."""

    detalles = DocumentoDetalleSerializer(many=True, read_only=True)
    tipo_display = serializers.CharField(source="get_tipo_display", read_only=True)
    estado_display = serializers.CharField(source="get_estado_display", read_only=True)

    class Meta:
        model = models.Documento
        fields = [
            "id", "tipo", "tipo_display", "estado", "estado_display",
            "emisor", "resolucion", "adquiriente",
            "prefijo", "consecutivo", "numero", "cufe_cude", "track_id",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "valor_bruto", "total_impuestos", "total_descuentos", "total_cargos",
            "total_a_pagar", "documento_referencia", "observaciones", "detalles",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = [
            "estado", "cufe_cude", "track_id", "valor_bruto", "total_impuestos",
            "total_a_pagar", "creado_en", "actualizado_en",
        ]


class DocumentoCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con detalles e impuestos anidados.

    Calcula automáticamente los totales a partir de los detalles.
    """

    detalles = DocumentoDetalleSerializer(many=True)

    class Meta:
        model = models.Documento
        fields = [
            "id", "tipo", "emisor", "resolucion", "adquiriente",
            "prefijo", "consecutivo", "numero",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "total_descuentos", "total_cargos", "documento_referencia",
            "observaciones", "detalles",
        ]

    def validate_detalles(self, detalles):
        if not detalles:
            raise serializers.ValidationError("El documento debe tener al menos un detalle.")
        return detalles

    @transaction.atomic
    def create(self, validated_data):
        detalles_data = validated_data.pop("detalles")
        descuentos = validated_data.get("total_descuentos", Decimal("0")) or Decimal("0")
        cargos = validated_data.get("total_cargos", Decimal("0")) or Decimal("0")

        valor_bruto = Decimal("0")
        total_impuestos = Decimal("0")

        documento = models.Documento.objects.create(
            valor_bruto=Decimal("0"), total_impuestos=Decimal("0"),
            total_a_pagar=Decimal("0"), **validated_data,
        )

        for detalle_data in detalles_data:
            impuestos_data = detalle_data.pop("impuestos", [])
            detalle = models.DocumentoDetalle.objects.create(documento=documento, **detalle_data)
            valor_bruto += detalle.valor_total
            for imp in impuestos_data:
                impuesto = models.DocumentoDetalleImpuesto.objects.create(detalle=detalle, **imp)
                total_impuestos += impuesto.valor

        documento.valor_bruto = valor_bruto
        documento.total_impuestos = total_impuestos
        documento.total_a_pagar = valor_bruto - descuentos + cargos + total_impuestos
        documento.save(update_fields=["valor_bruto", "total_impuestos", "total_a_pagar"])
        return documento

    def to_representation(self, instance):
        return DocumentoSerializer(instance, context=self.context).data
