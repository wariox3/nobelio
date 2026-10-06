"""Catálogo: evento RADIAN que registra el adquiriente."""
from .base import ElementoCatalogo


class EventoRadian(ElementoCatalogo):
    """Evento del adquiriente sobre una factura recibida: 030 a 033.

    El ``nombre`` es el literal de ``cac:Response/cbc:Description`` de los
    ejemplos oficiales, y va tal cual al XML. Solo los del adquiriente: el 034
    (aceptación tácita) es del facturador y los de título valor (035 a 051)
    quedan fuera (``docs/recepcion.md``).
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_evento_radian"
        verbose_name = "evento RADIAN"
        verbose_name_plural = "eventos RADIAN"
