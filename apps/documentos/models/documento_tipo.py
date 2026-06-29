"""Tipo de documento electrónico (discriminador interno + InvoiceTypeCode DIAN)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class DocumentoTipo(ModeloConFechas):
    """Tipo de documento electrónico.

    ``codigo`` es el discriminador interno que selecciona la lógica de
    generación (constructor UBL); ``codigo_dian`` es el InvoiceTypeCode oficial.
    """

    class Codigo(models.TextChoices):
        FACTURA_VENTA = "factura_venta", "Factura de venta"
        NOTA_CREDITO = "nota_credito", "Nota crédito"
        NOTA_DEBITO = "nota_debito", "Nota débito"
        DOCUMENTO_SOPORTE = "documento_soporte", "Documento soporte"
        NOMINA = "nomina", "Nómina electrónica"

    codigo = models.CharField(
        "código", max_length=30, unique=True, choices=Codigo.choices,
        help_text="Discriminador interno que define la lógica de generación.",
    )
    nombre = models.CharField("nombre", max_length=100)
    codigo_dian = models.CharField(
        "código DIAN (InvoiceTypeCode)", max_length=2, blank=True,
        help_text="InvoiceTypeCode oficial (01, 02, 91, 92, 05).",
    )
    activo = models.BooleanField("activo", default=True)

    class Meta:
        db_table = "doc_documento_tipo"
        verbose_name = "tipo de documento"
        verbose_name_plural = "tipos de documento"
        ordering = ["codigo"]

    def __str__(self):
        return self.nombre
