"""Catálogo: tipo de contrato."""
from .base import ElementoCatalogo


class TipoContrato(ElementoCatalogo):
    """Tipo de contrato del trabajador. Lista TipoContrato.

    Del anexo de nómina, numeral 5.5.2. No viene en `.gc`: se siembra por
    migración, como el resto de catálogos de nómina.
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_contrato"
        verbose_name = "tipo de contrato"
        verbose_name_plural = "tipos de contrato"
