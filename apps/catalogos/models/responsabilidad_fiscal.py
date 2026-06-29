"""Catálogo: responsabilidad fiscal."""
from .base import ElementoCatalogo


class ResponsabilidadFiscal(ElementoCatalogo):
    """Responsabilidad tributaria (cbc:TaxLevelCode). Lista TipoResponsabilidad.

    El código puede ser alfanumérico (p. ej. ``O-13``, ``R-99-PN``).
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_responsabilidad_fiscal"
        verbose_name = "responsabilidad fiscal"
        verbose_name_plural = "responsabilidades fiscales"
