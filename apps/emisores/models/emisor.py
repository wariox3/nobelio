"""Modelo del emisor (Obligado a Facturar Electrónicamente — OFE)."""
from django.conf import settings
from django.db import models

from apps.nucleo.models import Ambiente, ModeloConFechas
from apps.utilidades.nit import dv_de_entidad


def ambiente_por_defecto():
    """El ambiente configurado en el servidor al dar de alta al emisor.

    El ajuste global pasa a decidir solo con qué ambiente **nace** un emisor;
    contra qué servidor se emite lo dice ya el emisor, y el documento se lo
    queda al crearse. Callable y no el valor a secas para que la migración
    guarde la referencia a la función.
    """
    return settings.DIAN_ENVIRONMENT


class Emisor(ModeloConFechas):
    """Obligado a Facturar Electrónicamente (OFE).

    Corresponde a ``cac:AccountingSupplierParty`` en el XML UBL.
    """

    razon_social = models.CharField("razón social", max_length=450)
    numero_identificacion = models.CharField("número de identificación", max_length=20, help_text="NIT sin puntos, sin guiones y sin dígito de verificación.",)
    digito_verificacion = models.CharField("dígito de verificación", max_length=1, blank=True)
    direccion = models.CharField("dirección", max_length=255)
    correo_copia = models.CharField(
        "correo en copia", max_length=255, blank=True,
        help_text="Copia de las notificaciones al adquiriente. Varios correos "
        "separados por punto y coma. Vacío = no se envía copia.",
    )
    codigo_postal = models.CharField(
        "código postal", max_length=10, blank=True,
        help_text="cbc:PostalZone de las direcciones del XML.",
    )
    telefono = models.CharField("teléfono", max_length=50, blank=True)
    correo = models.EmailField("correo electrónico", blank=True)
    activo = models.BooleanField("activo", default=True)
    habilitado_facturacion = models.BooleanField(
        "habilitado para facturar", default=False,
        help_text="Se marca al enviar el Set de Pruebas a la DIAN. Es el "
        "paso que separa al emisor recién dado de alta del que ya salió a "
        "producción.",
    )
    habilitado_nomina = models.BooleanField(
        "habilitado para nómina", default=False,
        help_text="La nómina se habilita aparte de la facturación y sin Set de "
        "Pruebas: el trámite es en el portal de la DIAN, así que esta bandera "
        "no la marca el sistema por sí solo. Sin ella, la DIAN rechaza con la "
        "regla 92 ('El Emisor del Documento no se encuentra Habilitado').",
    )
    habilitado_documento_equivalente = models.BooleanField(
        "habilitado para documento equivalente", default=False,
        help_text="El documento equivalente P.O.S. sí tiene Set de Pruebas "
        "propio (`SendTestSetAsync`), con su TestSetId: la DIAN exige emitir "
        "los documentos y las notas de ajuste que pida el sistema antes de "
        "pasar a producción.",
    )

    ambiente_facturacion = models.PositiveSmallIntegerField(
        "ambiente DIAN de facturación", choices=Ambiente.choices,
        default=ambiente_por_defecto,
        help_text="Contra qué servidor de la DIAN salen las facturas, notas y "
        "documentos soporte de este emisor. Es por emisor y no del despliegue: "
        "unos pueden seguir en habilitación mientras otros ya están en "
        "producción.",
    )
    ambiente_nomina = models.PositiveSmallIntegerField(
        "ambiente DIAN de nómina", choices=Ambiente.choices,
        default=ambiente_por_defecto,
        help_text="Lo mismo para la nómina, que la DIAN habilita aparte: el "
        "mismo emisor puede estar en producción para factura y todavía en "
        "habilitación para nómina.",
    )
    ambiente_documento_equivalente = models.PositiveSmallIntegerField(
        "ambiente DIAN de documento equivalente", choices=Ambiente.choices,
        default=ambiente_por_defecto,
        help_text="Y lo mismo para el documento equivalente P.O.S., que tiene "
        "su propia habilitación (Res. 000165/2023, numeral 4). Son tres "
        "ambientes independientes, no uno del despliegue.",
    )

    # --- Relaciones ---
    # Dueño del emisor. PROTECT y no CASCADE: de aquí cuelgan documentos
    # fiscales de terceros, así que borrar a una persona no puede llevarse por
    # delante ese histórico. Para dar de baja está `is_active`.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="emisores_propios",
        verbose_name="usuario",
        help_text="Usuario dueño del emisor.",
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
            # Unicidad global: un NIT existe una sola vez en la plataforma. Antes
            # era por cuenta, lo que permitía tener el mismo NIT dado de alta en
            # dos integraciones a la vez (p. ej. mientras un cliente migra de
            # proveedor). Eso ya no se puede.
            models.UniqueConstraint(
                fields=["tipo_identificacion", "numero_identificacion"],
                name="emisor_identificacion_unica",
            )
        ]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"
