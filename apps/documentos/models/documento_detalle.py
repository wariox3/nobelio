"""Detalle (línea) de un documento electrónico."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .documento import Documento


class DocumentoDetalle(ModeloConFechas):
    """Detalle / línea de un documento (``cac:InvoiceLine``)."""

    # --- Atributos ---
    numero_linea = models.PositiveIntegerField("número de línea")
    descripcion = models.CharField("descripción", max_length=500)
    codigo_producto = models.CharField("código del producto", max_length=100, blank=True)
    cantidad = models.DecimalField(
        "cantidad", max_digits=18, decimal_places=6, default=1
    )
    valor_unitario = models.DecimalField(
        "valor unitario", max_digits=18, decimal_places=6, default=0
    )
    valor_total = models.DecimalField(
        "valor total del detalle", max_digits=18, decimal_places=2, default=0,
        help_text="cantidad × valor unitario (LineExtensionAmount).",
    )
    descuento = models.DecimalField(
        "descuento", max_digits=18, decimal_places=2, default=0
    )

    # --- Datos de negocio (opcionales, van al XML si vienen) ---
    nota = models.CharField(
        "nota de la línea", max_length=500, blank=True,
        help_text="cbc:Note de la línea: aclaración libre sobre este ítem.",
    )
    marca = models.CharField(
        "marca", max_length=100, blank=True, help_text="cbc:BrandName del ítem.",
    )
    modelo = models.CharField(
        "modelo", max_length=100, blank=True, help_text="cbc:ModelName del ítem.",
    )
    centro_costo = models.CharField(
        "centro de costo", max_length=50, blank=True,
        help_text="cbc:AccountingCostCode: a qué centro de costo se imputa.",
    )
    periodo_desde = models.DateField(
        "periodo facturado desde", null=True, blank=True,
        help_text="cac:InvoicePeriod/cbc:StartDate: para servicios por periodo.",
    )
    periodo_hasta = models.DateField(
        "periodo facturado hasta", null=True, blank=True,
        help_text="cac:InvoicePeriod/cbc:EndDate.",
    )

    # --- Relaciones ---
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name="detalles", verbose_name="documento",
    )
    unidad_medida = models.ForeignKey(
        "catalogos.UnidadMedida", on_delete=models.PROTECT,
        related_name="detalles", verbose_name="unidad de medida",
    )

    class Meta:
        db_table = "doc_documento_detalle"
        verbose_name = "detalle de documento"
        verbose_name_plural = "detalles de documento"
        ordering = ["numero_linea"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "numero_linea"],
                name="documento_detalle_numero_unico",
            )
        ]

    def __str__(self):
        return f"Detalle {self.numero_linea} de {self.documento.numero}"
