"""Catálogo: tipo de identificación fiscal."""
from .base import ElementoCatalogo


class TipoIdentificacion(ElementoCatalogo):
    """Tipo de identificación fiscal (cédula, NIT, etc.). Lista TipoIdFiscal."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_identificacion"
        verbose_name = "tipo de identificación"
        verbose_name_plural = "tipos de identificación"
