"""Catálogo: moneda."""
from .base import ElementoCatalogo


class Moneda(ElementoCatalogo):
    """Moneda (cbc:DocumentCurrencyCode), ISO 4217. Lista TipoMoneda."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_moneda"
        verbose_name = "moneda"
        verbose_name_plural = "monedas"
