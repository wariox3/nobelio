"""Catálogo: concepto del reclamo de una factura (evento 031)."""
from .base import ElementoCatalogo


class ConceptoReclamo(ElementoCatalogo):
    """Por qué se reclama una factura. Lista ``Concepto de Reclamo``.

    Va en ``cbc:ResponseCode/@listID`` (el código) y ``@name`` (el nombre) del
    evento 031.
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_concepto_reclamo"
        verbose_name = "concepto de reclamo"
        verbose_name_plural = "conceptos de reclamo"
