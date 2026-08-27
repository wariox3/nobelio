"""Catálogo: tipo de trabajador."""
from .base import ElementoCatalogo


class TipoTrabajador(ElementoCatalogo):
    """Tipo de cotizante ante la seguridad social. Lista TipoTrabajador.

    Del anexo de nómina, numeral 5.5.3. No viene en `.gc`: se siembra por
    migración, como el resto de catálogos de nómina.
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_trabajador"
        verbose_name = "tipo de trabajador"
        verbose_name_plural = "tipos de trabajador"
