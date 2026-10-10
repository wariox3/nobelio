"""Resolución de numeración (autorización de rango y clave técnica)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor


class Resolucion(ModeloConFechas):
    """Resolución de numeración (autorización de rango y clave técnica).

    La ``clave_tecnica`` es la que se usa para calcular el CUFE y NO viaja en
    el XML. Se obtiene de la consulta del rango de numeración ante la DIAN.
    """

    # --- Atributos ---
    numero_resolucion = models.CharField("número de resolución", max_length=50)
    fecha_resolucion = models.DateField("fecha de la resolución")

    prefijo = models.CharField("prefijo", max_length=10, blank=True)
    rango_desde = models.PositiveBigIntegerField("rango desde")
    rango_hasta = models.PositiveBigIntegerField("rango hasta")
    # El siguiente número a usar, no el último usado: una resolución recién
    # creada apunta a `rango_desde` (lo pone `save`), y `rango_hasta + 1`
    # significa que el rango se agotó.
    consecutivo_actual = models.PositiveBigIntegerField(
        "consecutivo actual", blank=True,
        help_text="Siguiente consecutivo a usar. Vacío toma el rango desde.",
    )

    clave_tecnica = models.CharField("clave técnica", max_length=255, blank=True)

    vigente_desde = models.DateField("vigente desde")
    vigente_hasta = models.DateField("vigente hasta")

    activa = models.BooleanField("activa", default=True)

    # --- Relaciones ---
    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="resoluciones",
        verbose_name="emisor",
    )
    tipo_factura = models.ForeignKey(
        "catalogos.TipoFactura",
        on_delete=models.PROTECT,
        related_name="resoluciones",
        verbose_name="tipo de factura",
    )

    class Meta:
        db_table = "emi_resolucion"
        verbose_name = "resolución"
        verbose_name_plural = "resoluciones"
        ordering = ["-fecha_resolucion"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "tipo_factura", "prefijo", "numero_resolucion"],
                name="resolucion_unica_por_emisor",
            )
        ]

    def save(self, *args, **kwargs):
        if self.consecutivo_actual is None:
            self.consecutivo_actual = self.rango_desde
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Res. {self.numero_resolucion} {self.prefijo} ({self.emisor})"
