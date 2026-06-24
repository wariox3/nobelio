"""Adquirente: cliente / receptor del documento electrónico."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Adquirente(ModeloConFechas):
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
        related_name="adquirentes",
        verbose_name="tipo de identificación",
    )
    tipo_organizacion = models.ForeignKey(
        "catalogos.TipoOrganizacion",
        on_delete=models.PROTECT,
        related_name="adquirentes",
        verbose_name="tipo de organización",
    )
    responsabilidades = models.ManyToManyField(
        "catalogos.ResponsabilidadFiscal",
        related_name="adquirentes",
        verbose_name="responsabilidades fiscales",
        blank=True,
    )
    pais = models.ForeignKey(
        "catalogos.Pais", on_delete=models.PROTECT,
        related_name="adquirentes", verbose_name="país",
    )
    departamento = models.ForeignKey(
        "catalogos.Departamento", on_delete=models.PROTECT,
        related_name="adquirentes", verbose_name="departamento",
        null=True, blank=True,
    )
    municipio = models.ForeignKey(
        "catalogos.Municipio", on_delete=models.PROTECT,
        related_name="adquirentes", verbose_name="municipio",
        null=True, blank=True,
    )

    class Meta:
        verbose_name = "adquirente"
        verbose_name_plural = "adquirentes"
        ordering = ["razon_social"]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
