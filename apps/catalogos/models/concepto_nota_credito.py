"""Catálogo: concepto de corrección de nota crédito."""
from .base import ElementoCatalogo


class ConceptoNotaCredito(ElementoCatalogo):
    """Concepto de corrección de una nota crédito. Lista ConceptoNotaCredito."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_concepto_nota_credito"
        verbose_name = "concepto de nota crédito"
        verbose_name_plural = "conceptos de nota crédito"
