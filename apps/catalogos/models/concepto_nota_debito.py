"""Catálogo: concepto de corrección de nota débito."""
from .base import ElementoCatalogo


class ConceptoNotaDebito(ElementoCatalogo):
    """Concepto de corrección de una nota débito. Lista ConceptoNotaDebito."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_concepto_nota_debito"
        verbose_name = "concepto de nota débito"
        verbose_name_plural = "conceptos de nota débito"
