"""Catálogos de uso comercial (unidades, pagos, moneda)."""
from .base import ElementoCatalogo


class UnidadMedida(ElementoCatalogo):
    """Unidad de medida (cbc:unitCode). Lista UnidadesMedida."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "unidad de medida"
        verbose_name_plural = "unidades de medida"


class FormaPago(ElementoCatalogo):
    """Forma de pago: contado / crédito (cbc:PaymentMeansCode). Lista FormasPago."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "forma de pago"
        verbose_name_plural = "formas de pago"


class MedioPago(ElementoCatalogo):
    """Medio de pago: efectivo, transferencia, etc. Lista MediosPago."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "medio de pago"
        verbose_name_plural = "medios de pago"


class Moneda(ElementoCatalogo):
    """Moneda (cbc:DocumentCurrencyCode), ISO 4217. Lista TipoMoneda."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "moneda"
        verbose_name_plural = "monedas"
