"""Adquiriente: receptor del documento electrónico.

No es una cartera de clientes: los datos del receptor se piden en cada documento
y se guardan pegados a él. Así el documento conserva al receptor tal como se
facturó —que es lo que quedó firmado en el XML y entró en el CUFE—, y editar un
cliente no reescribe la historia de las facturas ya emitidas.
"""
from django.db import models

from apps.nucleo.models import ModeloConFechas
from apps.utilidades.nit import dv_de_entidad


class Adquiriente(ModeloConFechas):
    """Receptor del documento (``cac:AccountingCustomerParty``).

    Va en tabla aparte y no en columnas del documento por las responsabilidades
    fiscales, que son una relación múltiple, y para no cargar `Documento` con
    quince campos del receptor. La relación es 1:1: se llega con
    ``documento.adquiriente``.
    """

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
    documento = models.OneToOneField(
        "documentos.Documento",
        on_delete=models.CASCADE,
        related_name="adquiriente",
        verbose_name="documento",
    )
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

    def save(self, *args, **kwargs):
        # El DV se calcula, no se recibe: la DIAN lo comprueba contra el NIT y
        # un valor tecleado a mano (o traído del RUES) rechaza el documento.
        self.digito_verificacion = dv_de_entidad(self)
        super().save(*args, **kwargs)

    class Meta:
        db_table = "doc_adquiriente"
        verbose_name = "adquiriente"
        verbose_name_plural = "adquirientes"
        ordering = ["razon_social"]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
