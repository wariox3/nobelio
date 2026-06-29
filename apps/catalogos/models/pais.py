"""Catálogo: país."""
from .base import ElementoCatalogo


class Pais(ElementoCatalogo):
    """País (cac:Country), ISO 3166. Lista Paises."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_pais"
        verbose_name = "país"
        verbose_name_plural = "países"
