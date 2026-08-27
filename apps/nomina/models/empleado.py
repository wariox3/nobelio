"""Trabajador al que se le paga la nómina (elemento ``Trabajador`` del XML)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Empleado(ModeloConFechas):
    """Trabajador de un emisor, con su contrato y su forma de cobro.

    En la nómina electrónica la contraparte no llega en cada documento como el
    adquiriente de la factura: es una relación laboral que dura y de la que se
    emite un comprobante por periodo. Por eso vive en su propia tabla y el
    documento solo la referencia.

    Los datos que el XML reparte entre ``Trabajador``, ``Periodo`` (fechas de
    ingreso y retiro) y ``Pago`` están todos aquí, porque todos son del contrato
    y no del periodo que se liquida.
    """

    class TipoCuenta(models.TextChoices):
        AHORROS = "ahorros", "Ahorros"
        CORRIENTE = "corriente", "Corriente"

    # --- Identificación ---
    # El anexo admite para el trabajador un tipo que no existe en el RUT (91,
    # NUIP), así que este catálogo se queda corto; se decide al sembrarlo.
    tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="tipo de identificación",
    )
    numero_documento = models.CharField(
        "número de documento", max_length=20,
        help_text="Sin puntos, sin guiones y sin dígito de verificación.",
    )
    primer_apellido = models.CharField("primer apellido", max_length=100)
    segundo_apellido = models.CharField("segundo apellido", max_length=100, blank=True)
    primer_nombre = models.CharField("primer nombre", max_length=100)
    # El XML no tiene "segundo nombre" sino un campo para todo lo demás.
    otros_nombres = models.CharField("otros nombres", max_length=100, blank=True)

    # --- Contrato ---
    codigo_trabajador = models.CharField(
        "código del trabajador", max_length=20, blank=True,
        help_text="Código interno del empleador; viaja en el XML.",
    )
    alto_riesgo_pension = models.BooleanField("alto riesgo de pensión", default=False)
    salario_integral = models.BooleanField("salario integral", default=False)
    sueldo = models.DecimalField("sueldo", max_digits=15, decimal_places=2)
    fecha_ingreso = models.DateField("fecha de ingreso")
    fecha_retiro = models.DateField("fecha de retiro", null=True, blank=True)

    # --- Lugar de trabajo ---
    direccion = models.CharField("dirección del lugar de trabajo", max_length=255)

    # --- Pago ---
    banco = models.CharField("banco", max_length=100, blank=True)
    tipo_cuenta = models.CharField(
        "tipo de cuenta", max_length=10, choices=TipoCuenta.choices, blank=True,
    )
    numero_cuenta = models.CharField("número de cuenta", max_length=30, blank=True)

    activo = models.BooleanField("activo", default=True)

    # --- Relaciones ---
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="emisor",
    )
    tipo_trabajador = models.ForeignKey(
        "catalogos.TipoTrabajador", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="tipo de trabajador",
    )
    subtipo_trabajador = models.ForeignKey(
        "catalogos.SubTipoTrabajador", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="subtipo de trabajador",
    )
    tipo_contrato = models.ForeignKey(
        "catalogos.TipoContrato", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="tipo de contrato",
    )
    pais = models.ForeignKey(
        "catalogos.Pais", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="país del lugar de trabajo",
    )
    departamento = models.ForeignKey(
        "catalogos.Departamento", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="departamento del lugar de trabajo",
    )
    municipio = models.ForeignKey(
        "catalogos.Municipio", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="municipio del lugar de trabajo",
    )
    # Mismas listas que la factura: el anexo de nómina remite a FormasPago
    # (numeral 5.3.3.1) y a MediosPago (5.3.3.2).
    forma_pago = models.ForeignKey(
        "catalogos.FormaPago", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="forma de pago",
    )
    medio_pago = models.ForeignKey(
        "catalogos.MedioPago", on_delete=models.PROTECT,
        related_name="empleados", verbose_name="medio de pago",
    )

    class Meta:
        db_table = "nom_empleado"
        verbose_name = "empleado"
        verbose_name_plural = "empleados"
        ordering = ["primer_apellido", "primer_nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "tipo_identificacion", "numero_documento"],
                name="empleado_identificacion_unica_por_emisor",
            )
        ]

    @property
    def nombre_completo(self) -> str:
        partes = [self.primer_nombre, self.otros_nombres,
                  self.primer_apellido, self.segundo_apellido]
        return " ".join(p for p in partes if p)

    def __str__(self):
        return f"{self.nombre_completo} ({self.numero_documento})"
