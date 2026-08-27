"""Catálogo: periodo de nómina."""
from .base import ElementoCatalogo


class PeriodoNomina(ElementoCatalogo):
    """Periodicidad del pago (semanal, quincenal, mensual…). Lista PeriodoNomina.

    Del anexo de nómina, numeral 5.5.1. Como los demás catálogos de nómina, la
    DIAN no lo publica en `.gc` sino dentro del PDF, así que se siembra por
    migración y no con ``cargar_catalogos``.
    """

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_periodo_nomina"
        verbose_name = "periodo de nómina"
        verbose_name_plural = "periodos de nómina"
