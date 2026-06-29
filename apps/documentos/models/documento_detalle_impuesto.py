"""Impuesto aplicado a un detalle de documento."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .documento_detalle import DocumentoDetalle


class DocumentoDetalleImpuesto(ModeloConFechas):
    """Impuesto aplicado a un detalle (``cac:TaxTotal`` dentro de la línea)."""

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
    detalle = models.ForeignKey(
        DocumentoDetalle, on_delete=models.CASCADE,
        related_name="impuestos", verbose_name="detalle",
    )
    tributo = models.ForeignKey(
        "catalogos.Tributo", on_delete=models.PROTECT,
        related_name="impuestos_detalle", verbose_name="tributo",
    )

    class Meta:
        db_table = "doc_documento_detalle_impuesto"
        verbose_name = "impuesto de detalle"
        verbose_name_plural = "impuestos de detalle"

    def __str__(self):
        return f"{self.tributo} {self.tarifa}% = {self.valor}"
