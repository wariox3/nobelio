"""Catálogos relativos a tipos de documento y conceptos de notas."""
from .base import ElementoCatalogo


class TipoFactura(ElementoCatalogo):
    """Tipo de documento / factura (cbc:InvoiceTypeCode). Lista TipoDocumento."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_factura"
        verbose_name = "tipo de factura"
        verbose_name_plural = "tipos de factura"


class ConceptoNotaCredito(ElementoCatalogo):
    """Concepto de corrección de una nota crédito. Lista ConceptoNotaCredito."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_concepto_nota_credito"
        verbose_name = "concepto de nota crédito"
        verbose_name_plural = "conceptos de nota crédito"


class ConceptoNotaDebito(ElementoCatalogo):
    """Concepto de corrección de una nota débito. Lista ConceptoNotaDebito."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_concepto_nota_debito"
        verbose_name = "concepto de nota débito"
        verbose_name_plural = "conceptos de nota débito"
