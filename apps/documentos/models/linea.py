"""Líneas de documento e impuestos por línea."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .documento import DocumentoElectronico


class LineaDocumento(ModeloConFechas):
    """Línea / ítem de un documento (``cac:InvoiceLine``)."""

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
        "valor total de la línea", max_digits=18, decimal_places=2, default=0,
        help_text="cantidad × valor unitario (LineExtensionAmount).",
    )
    descuento = models.DecimalField(
        "descuento", max_digits=18, decimal_places=2, default=0
    )

    # --- Relaciones ---
    documento = models.ForeignKey(
        DocumentoElectronico, on_delete=models.CASCADE,
        related_name="lineas", verbose_name="documento",
    )
    unidad_medida = models.ForeignKey(
        "catalogos.UnidadMedida", on_delete=models.PROTECT,
        related_name="lineas", verbose_name="unidad de medida",
    )

    class Meta:
        db_table = "doc_linea_documento"
        verbose_name = "línea de documento"
        verbose_name_plural = "líneas de documento"
        ordering = ["numero_linea"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "numero_linea"],
                name="linea_numero_unico_por_documento",
            )
        ]

    def __str__(self):
        return f"Línea {self.numero_linea} de {self.documento.numero}"


class ImpuestoLinea(ModeloConFechas):
    """Impuesto aplicado a una línea (``cac:TaxTotal`` dentro de la línea)."""

    # --- Atributos ---
    base_gravable = models.DecimalField(
        "base gravable", max_digits=18, decimal_places=2, default=0
    )
    tarifa = models.DecimalField(
        "tarifa (%)", max_digits=7, decimal_places=4, default=0
    )
    valor = models.DecimalField(
        "valor del impuesto", max_digits=18, decimal_places=2, default=0
    )

    # --- Relaciones ---
    linea = models.ForeignKey(
        LineaDocumento, on_delete=models.CASCADE,
        related_name="impuestos", verbose_name="línea",
    )
    tributo = models.ForeignKey(
        "catalogos.Tributo", on_delete=models.PROTECT,
        related_name="impuestos_linea", verbose_name="tributo",
    )

    class Meta:
        db_table = "doc_impuesto_linea"
        verbose_name = "impuesto de línea"
        verbose_name_plural = "impuestos de línea"

    def __str__(self):
        return f"{self.tributo} {self.tarifa}% = {self.valor}"
