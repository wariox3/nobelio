"""Serializers de los documentos recibidos."""
from rest_framework import serializers

from apps.recepcion.models import Documento


class DocumentoRecibidoResumenSerializer(serializers.ModelSerializer):
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


class DocumentoRecibidoSerializer(serializers.ModelSerializer):
    """Un documento recibido. Los archivos no van aquí: se bajan por sus rutas
    (``xml/``, ``xml-factura/`` y ``pdf/``)."""

    documento_tipo = serializers.SlugRelatedField(slug_field="codigo", read_only=True)
    moneda = serializers.SlugRelatedField(slug_field="codigo", read_only=True)
    tiene_xml_factura = serializers.SerializerMethodField()
    tiene_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Documento
        fields = [
            "id", "emisor", "correo", "documento_tipo", "tipo_codigo_dian",
            "numero", "cufe_cude", "fecha_emision", "hora_emision",
            "proveedor_numero_identificacion", "proveedor_digito_verificacion",
            "proveedor_razon_social", "receptor_numero_identificacion", "moneda",
            "valor_bruto", "total_impuestos", "total_a_pagar",
            "validacion_codigo", "fecha_validacion", "tiene_xml_factura",
            "tiene_pdf", "creado_en",
        ]
        read_only_fields = fields

    def get_tiene_xml_factura(self, obj) -> bool:
        return _tiene(obj, "xml_documento")

    def get_tiene_pdf(self, obj) -> bool:
        return _tiene(obj, "pdf")


def _tiene(documento, rol):
    # Sobre `adjuntos.all()` y no con un filtro: así usa el prefetch de la vista.
    return any(adjunto.rol == rol for adjunto in documento.adjuntos.all())
