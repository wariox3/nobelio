"""Serializers de lectura y creación del documento electrónico."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.documentos import models

from .linea import LineaDocumentoSerializer


class DocumentoElectronicoSerializer(serializers.ModelSerializer):
    """Serializer de lectura del documento, con líneas anidadas."""

    lineas = LineaDocumentoSerializer(many=True, read_only=True)
    tipo_display = serializers.CharField(source="get_tipo_display", read_only=True)
    estado_display = serializers.CharField(source="get_estado_display", read_only=True)

    class Meta:
        model = models.DocumentoElectronico
        fields = [
            "id", "tipo", "tipo_display", "estado", "estado_display",
            "emisor", "resolucion", "adquirente",
            "prefijo", "consecutivo", "numero", "cufe_cude",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "valor_bruto", "total_impuestos", "total_descuentos", "total_cargos",
            "total_a_pagar", "documento_referencia", "observaciones", "lineas",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = [
            "estado", "cufe_cude", "valor_bruto", "total_impuestos",
            "total_a_pagar", "creado_en", "actualizado_en",
        ]


class DocumentoCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con líneas e impuestos anidados.

    Calcula automáticamente los totales a partir de las líneas.
    """

    lineas = LineaDocumentoSerializer(many=True)

    class Meta:
        model = models.DocumentoElectronico
        fields = [
            "id", "tipo", "emisor", "resolucion", "adquirente",
            "prefijo", "consecutivo", "numero",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "total_descuentos", "total_cargos", "documento_referencia",
            "observaciones", "lineas",
        ]

    def validate_lineas(self, lineas):
        if not lineas:
            raise serializers.ValidationError("El documento debe tener al menos una línea.")
        return lineas

    @transaction.atomic
    def create(self, validated_data):
        lineas_data = validated_data.pop("lineas")
        descuentos = validated_data.get("total_descuentos", Decimal("0")) or Decimal("0")
        cargos = validated_data.get("total_cargos", Decimal("0")) or Decimal("0")

        valor_bruto = Decimal("0")
        total_impuestos = Decimal("0")

        documento = models.DocumentoElectronico.objects.create(
            valor_bruto=Decimal("0"), total_impuestos=Decimal("0"),
            total_a_pagar=Decimal("0"), **validated_data,
        )

        for linea_data in lineas_data:
            impuestos_data = linea_data.pop("impuestos", [])
            linea = models.LineaDocumento.objects.create(documento=documento, **linea_data)
            valor_bruto += linea.valor_total
            for imp in impuestos_data:
                impuesto = models.ImpuestoLinea.objects.create(linea=linea, **imp)
                total_impuestos += impuesto.valor

        documento.valor_bruto = valor_bruto
        documento.total_impuestos = total_impuestos
        documento.total_a_pagar = valor_bruto - descuentos + cargos + total_impuestos
        documento.save(update_fields=["valor_bruto", "total_impuestos", "total_a_pagar"])
        return documento

    def to_representation(self, instance):
        return DocumentoElectronicoSerializer(instance, context=self.context).data
