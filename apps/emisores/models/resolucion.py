"""Resolución de numeración (autorización de rango y clave técnica)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor


class ResolucionFacturacion(ModeloConFechas):
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

    clave_tecnica = models.CharField("clave técnica", max_length=255, blank=True)

    vigente_desde = models.DateField("vigente desde")
    vigente_hasta = models.DateField("vigente hasta")

    consecutivo_actual = models.PositiveBigIntegerField(
        "consecutivo actual", default=0,
        help_text="Último consecutivo emitido; 0 si aún no se ha emitido.",
    )
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
        db_table = "emi_resolucion_facturacion"
        verbose_name = "resolución de facturación"
        verbose_name_plural = "resoluciones de facturación"
        ordering = ["-fecha_resolucion"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "tipo_factura", "prefijo", "numero_resolucion"],
                name="resolucion_unica_por_emisor",
            )
        ]

    def __str__(self):
        return f"Res. {self.numero_resolucion} {self.prefijo} ({self.emisor})"

    @property
    def siguiente_consecutivo(self) -> int:
        """Calcula el siguiente número a emitir respetando el rango autorizado."""
        base = max(self.consecutivo_actual, self.rango_desde - 1)
        return base + 1
