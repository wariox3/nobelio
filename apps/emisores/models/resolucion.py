"""Resolución de numeración (autorización de rango y clave técnica)."""
from django.db import models
from django.db.models import Case, Value, When

from apps.nucleo.models import Ambiente, ModeloConFechas

from .emisor import Emisor

# Los tipos de factura de compras, por su id del catálogo —fijo, viene en la
# columna `id` del .gc—: 7 es el documento soporte (05) y 8 su nota de ajuste
# (95). Van por id y no por código porque una columna generada solo puede leer
# su propia fila, y de `tipo_factura` la fila solo tiene la llave.
TIPOS_FACTURA_DE_COMPRAS = (7, 8)


class Resolucion(ModeloConFechas):
    """Resolución de numeración (autorización de rango y clave técnica).

    La ``clave_tecnica`` es la que se usa para calcular el CUFE y NO viaja en
    el XML. Se obtiene de la consulta del rango de numeración ante la DIAN.
    """

    class Modulo(models.TextChoices):
        """Módulo de las integraciones y los ERP al que sirve la numeración.

        Compras es el documento soporte; facturación, todo lo demás: la factura
        de venta y el documento equivalente P.O.S. Nómina está para que la lista
        sea la misma que manejan los ERP, pero ninguna resolución cae ahí: la
        nómina electrónica no se numera con resolución.
        """

        FACTURACION = "facturacion", "Facturación"
        NOMINA = "nomina", "Nómina"
        COMPRAS = "compras", "Compras"

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

    # De qué ambiente de la DIAN es la numeración. No se deduce del número: la
    # del Set de Pruebas (18760000001) es de habilitación, pero también lo es
    # cualquier rango que la DIAN devuelva mientras el emisor no esté habilitado.
    ambiente = models.PositiveSmallIntegerField(
        "ambiente DIAN", choices=Ambiente.choices,
        help_text="1 producción, 2 habilitación. Lo pone la siembra del Set de "
        "Pruebas y la importación desde la DIAN; en el alta manual lo indica "
        "quien la crea.",
    )

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
    # Columna generada por la base de datos a partir de `tipo_factura`: se
    # puede filtrar e indexar como cualquier otra, pero no se escribe nunca, así
    # que no hay forma de que contradiga al tipo.
    modulo = models.GeneratedField(
        expression=Case(
            When(
                tipo_factura_id__in=TIPOS_FACTURA_DE_COMPRAS,
                then=Value(Modulo.COMPRAS),
            ),
            default=Value(Modulo.FACTURACION),
        ),
        output_field=models.CharField(
            "módulo", max_length=15, choices=Modulo.choices,
        ),
        db_persist=True,
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
