"""Serializers de lectura y creación del documento electrónico."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from apps.documentos import models

from .documento_detalle import DocumentoDetalleSerializer
from .documento_error import DocumentoErrorSerializer


class DocumentoSerializer(serializers.ModelSerializer):
    """Serializer de lectura del documento, con detalles anidados."""

    detalles = DocumentoDetalleSerializer(many=True, read_only=True)
    errores = DocumentoErrorSerializer(many=True, read_only=True)
    documento_tipo_nombre = serializers.CharField(
        source="documento_tipo.nombre", read_only=True
    )
    estado_codigo = serializers.CharField(source="estado.codigo", read_only=True)
    estado_descripcion = serializers.CharField(source="estado.descripcion", read_only=True)

    class Meta:
        model = models.Documento
        fields = [
            "id", "documento_tipo", "documento_tipo_nombre",
            "estado", "estado_codigo", "estado_descripcion",
            "emisor", "resolucion", "adquiriente",
            "prefijo", "consecutivo", "numero", "cufe_cude", "track_id",
            "fecha_validacion", "errores",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "valor_bruto", "total_impuestos", "total_descuentos", "total_cargos",
            "total_a_pagar", "documento_referencia", "observaciones", "detalles",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = [
            "estado", "cufe_cude", "track_id", "fecha_validacion",
            "valor_bruto", "total_impuestos",
            "total_a_pagar", "creado_en", "actualizado_en",
        ]


class DocumentoListaSerializer(DocumentoSerializer):
    """Versión para el listado: sin las líneas (``detalles``) anidadas."""

    detalles = None  # se quita el campo heredado

    class Meta(DocumentoSerializer.Meta):
        fields = [f for f in DocumentoSerializer.Meta.fields if f != "detalles"]


class DocumentoCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con detalles e impuestos anidados.

    Calcula automáticamente los totales a partir de los detalles.
    """

    detalles = DocumentoDetalleSerializer(many=True)

    class Meta:
        model = models.Documento
        fields = [
            "id", "documento_tipo", "emisor", "resolucion", "adquiriente",
            "prefijo", "consecutivo", "numero",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "total_descuentos", "total_cargos", "documento_referencia",
            "observaciones", "detalles",
        ]
        # Mensaje propio para la unicidad (emisor+prefijo+consecutivo+tipo) en vez
        # del genérico "deben formar un conjunto único".
        validators = [
            UniqueTogetherValidator(
                queryset=models.Documento.objects.all(),
                fields=["emisor", "prefijo", "consecutivo", "documento_tipo"],
                message="El documento ya fue creado.",
            )
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
