"""Adquiriente: cliente / receptor del documento electrónico."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Adquiriente(ModeloConFechas):
    """Cliente / receptor del documento (``cac:AccountingCustomerParty``)."""

    # --- Atributos ---
    razon_social = models.CharField("razón social", max_length=450)
    numero_identificacion = models.CharField(
        "número de identificación", max_length=20,
        help_text="Sin puntos, sin guiones y sin dígito de verificación.",
    )
    digito_verificacion = models.CharField(
        "dígito de verificación", max_length=1, blank=True
    )
    direccion = models.CharField("dirección", max_length=255, blank=True)
    telefono = models.CharField("teléfono", max_length=50, blank=True)
    correo = models.EmailField("correo electrónico", blank=True)

    # --- Relaciones ---
    tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion",
        on_delete=models.PROTECT,
        related_name="adquirientes",
        verbose_name="tipo de identificación",
    )
    tipo_organizacion = models.ForeignKey(
        "catalogos.TipoOrganizacion",
        on_delete=models.PROTECT,
        related_name="adquirientes",
        verbose_name="tipo de organización",
    )
    responsabilidades = models.ManyToManyField(
        "catalogos.ResponsabilidadFiscal",
        related_name="adquirientes",
        verbose_name="responsabilidades fiscales",
        blank=True,
    )
    pais = models.ForeignKey(
        "catalogos.Pais", on_delete=models.PROTECT,
        related_name="adquirientes", verbose_name="país",
    )
    departamento = models.ForeignKey(
        "catalogos.Departamento", on_delete=models.PROTECT,
        related_name="adquirientes", verbose_name="departamento",
        null=True, blank=True,
    )
    municipio = models.ForeignKey(
        "catalogos.Municipio", on_delete=models.PROTECT,
        related_name="adquirientes", verbose_name="municipio",
        null=True, blank=True,
    )

    class Meta:
        db_table = "doc_adquiriente"
        verbose_name = "adquiriente"
        verbose_name_plural = "adquirientes"
        ordering = ["razon_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo_identificacion", "numero_identificacion"],
                name="adquiriente_identificacion_unica",
            )
        ]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
