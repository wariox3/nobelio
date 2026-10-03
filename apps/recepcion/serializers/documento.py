"""Serializers de los documentos recibidos."""
from rest_framework import serializers

from apps.recepcion.models import Documento


class DocumentoResumenSerializer(serializers.ModelSerializer):
    """Lo que se muestra de cada documento dentro de su correo."""

    documento_tipo = serializers.SlugRelatedField(slug_field="codigo", read_only=True)

    class Meta:
        model = Documento
        fields = [
            "id", "emisor", "documento_tipo", "numero", "cufe_cude",
            "fecha_emision", "proveedor_numero_identificacion",
            "proveedor_razon_social", "total_a_pagar", "validacion_codigo",
        ]
        read_only_fields = fields
