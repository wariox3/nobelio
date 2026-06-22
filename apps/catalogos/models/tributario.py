"""Catálogos tributarios."""
from .base import ElementoCatalogo


class Tributo(ElementoCatalogo):
    """Tributo / impuesto (cac:TaxScheme). Lista TipoImpuesto.

    Códigos relevantes para el CUFE: 01 IVA, 03 ICA, 04 INC.
    """

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tributo"
        verbose_name_plural = "tributos"
