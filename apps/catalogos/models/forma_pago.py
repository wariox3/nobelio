"""Catálogo: forma de pago."""
from .base import ElementoCatalogo


class FormaPago(ElementoCatalogo):
    """Forma de pago: contado / crédito (cbc:PaymentMeansCode). Lista FormasPago."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_forma_pago"
        verbose_name = "forma de pago"
        verbose_name_plural = "formas de pago"
