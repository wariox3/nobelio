"""Catálogo: unidad de medida."""
from .base import ElementoCatalogo


class UnidadMedida(ElementoCatalogo):
    """Unidad de medida (cbc:unitCode). Lista UnidadesMedida."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_unidad_medida"
        verbose_name = "unidad de medida"
        verbose_name_plural = "unidades de medida"
