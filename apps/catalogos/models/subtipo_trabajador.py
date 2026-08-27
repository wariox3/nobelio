"""Catálogo: subtipo de trabajador."""
from .base import ElementoCatalogo


class SubTipoTrabajador(ElementoCatalogo):
    """Subtipo de cotizante ("00 No aplica" en la mayoría). Lista SubTipoTrabajador.

    Del anexo de nómina, numeral 5.5.4. No viene en `.gc`: se siembra por
    migración, como el resto de catálogos de nómina.
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_subtipo_trabajador"
        verbose_name = "subtipo de trabajador"
        verbose_name_plural = "subtipos de trabajador"
