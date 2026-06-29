"""Catálogo: tipo de documento / factura."""
from .base import ElementoCatalogo


class TipoFactura(ElementoCatalogo):
    """Tipo de documento / factura (cbc:InvoiceTypeCode). Lista TipoDocumento."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_factura"
        verbose_name = "tipo de factura"
        verbose_name_plural = "tipos de factura"
