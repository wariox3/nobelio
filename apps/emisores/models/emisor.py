"""Modelo del emisor (Obligado a Facturar Electrónicamente — OFE)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Emisor(ModeloConFechas):
    """Obligado a Facturar Electrónicamente (OFE).

    Corresponde a ``cac:AccountingSupplierParty`` en el XML UBL.
    """

    razon_social = models.CharField("razón social", max_length=450)
    nombre_comercial = models.CharField("nombre comercial", max_length=450, blank=True)
    numero_identificacion = models.CharField("número de identificación", max_length=20, help_text="NIT sin puntos, sin guiones y sin dígito de verificación.",)
    digito_verificacion = models.CharField("dígito de verificación", max_length=1, blank=True)
    direccion = models.CharField("dirección", max_length=255)
    telefono = models.CharField("teléfono", max_length=50, blank=True)
    correo = models.EmailField("correo electrónico", blank=True)
    activo = models.BooleanField("activo", default=True)

    # --- Relaciones ---
    cuenta = models.ForeignKey(
        "cuentas.Cuenta",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="cuenta",
    )
    tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="tipo de identificación",
    )
    tipo_organizacion = models.ForeignKey(
        "catalogos.TipoOrganizacion",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="tipo de organización",
    )
    responsabilidades = models.ManyToManyField(
        "catalogos.ResponsabilidadFiscal",
        related_name="emisores",
        verbose_name="responsabilidades fiscales",
        blank=True,
    )
    pais = models.ForeignKey(
        "catalogos.Pais",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="país",
    )
    departamento = models.ForeignKey(
        "catalogos.Departamento",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="departamento",
    )
    municipio = models.ForeignKey(
        "catalogos.Municipio",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="municipio",
    )

    class Meta:
        verbose_name = "emisor"
        verbose_name_plural = "emisores"
        ordering = ["razon_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo_identificacion", "numero_identificacion"],
                name="emisor_identificacion_unica",
            )
        ]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
