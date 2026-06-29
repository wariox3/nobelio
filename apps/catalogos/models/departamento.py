"""Catálogo: departamento."""
from .base import ElementoCatalogo


class Departamento(ElementoCatalogo):
    """Departamento de Colombia (DANE, 2 dígitos). Lista Departamentos."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_departamento"
        verbose_name = "departamento"
        verbose_name_plural = "departamentos"
