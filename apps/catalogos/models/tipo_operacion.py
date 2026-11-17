"""Catálogo: tipo de operación de la factura de venta."""
from .base import ElementoCatalogo


class TipoOperacion(ElementoCatalogo):
    """Tipo de operación de la factura (``cbc:CustomizationID``). Lista
    ``TipoOperacionF``: 10 estándar, 09 AIU, 11 mandatos, 12 transporte, 13
    cambiario.

    Solo la de factura. Las notas y el documento soporte tienen sus propias
    listas, que chocan con esta en los códigos (el 10 y el 11 del documento
    soporte son residente y no residente) y que no se eligen: el código sale
    del propio documento —con o sin factura referenciada, país del vendedor—.
    """

    # Los códigos que el XML trata aparte (anexo FE 1.9, numeral 19.2 y regla
    # FAV03). El catálogo trae la lista entera de la DIAN, pero la API solo
    # admite los de `ADMITIDOS`: mandatos (11) y cambiario (13) exigen grupos
    # del XML que nobelio todavía no construye.
    AIU = "09"
    ESTANDAR = "10"
    TRANSPORTE = "12"
    ADMITIDOS = frozenset({AIU, ESTANDAR, TRANSPORTE})

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_operacion"
        verbose_name = "tipo de operación"
        verbose_name_plural = "tipos de operación"
