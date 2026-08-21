"""Modelo del emisor (Obligado a Facturar Electrónicamente — OFE)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas
from apps.utilidades.nit import dv_de_entidad


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
    habilitado_facturacion = models.BooleanField(
        "habilitado para facturar", default=False,
        help_text="Se marca al enviar el Set de Pruebas a la DIAN. Es el "
        "paso que separa al emisor recién dado de alta del que ya salió a "
        "producción.",
    )

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

    def save(self, *args, **kwargs):
        # El DV se calcula, no se recibe: la DIAN lo comprueba contra el NIT y
        # un valor tecleado a mano (o traído del RUES) rechaza el documento.
        self.digito_verificacion = dv_de_entidad(self)
        super().save(*args, **kwargs)

    class Meta:
        db_table = "emi_emisor"
        verbose_name = "emisor"
        verbose_name_plural = "emisores"
        ordering = ["razon_social"]
        constraints = [
            # La unicidad es por cuenta, no global: el mismo NIT puede estar dado
            # de alta en varias integraciones a la vez —una para facturación y
            # otra para nómina, o las dos a la vez mientras el cliente migra de
            # proveedor— y cada fila lleva sus propios datos (correo, resolución,
            # certificado). Lo que no puede repetirse entre esas filas es una
            # resolución de numeración activa; ver ResolucionFacturacion.
            models.UniqueConstraint(
                fields=["cuenta", "tipo_identificacion", "numero_identificacion"],
                name="emisor_identificacion_unica_por_cuenta",
            )
        ]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
