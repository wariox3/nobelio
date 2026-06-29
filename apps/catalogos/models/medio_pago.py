"""Catálogo: medio de pago."""
from .base import ElementoCatalogo


class MedioPago(ElementoCatalogo):
    """Medio de pago: efectivo, transferencia, etc. Lista MediosPago."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_medio_pago"
        verbose_name = "medio de pago"
        verbose_name_plural = "medios de pago"
