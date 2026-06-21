"""
Modelos de emisores (Obligados a Facturar Electrónicamente — OFE).

Incluye los datos del facturador, su software registrado en la DIAN, el
certificado digital de firma y las resoluciones de numeración (rangos y clave
técnica).
"""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Emisor(ModeloConFechas):
    """Obligado a Facturar Electrónicamente (OFE).

    Corresponde a ``cac:AccountingSupplierParty`` en el XML UBL.
    """

    razon_social = models.CharField("razón social", max_length=450)
    nombre_comercial = models.CharField(
        "nombre comercial", max_length=450, blank=True
    )

    tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion",
        on_delete=models.PROTECT,
        related_name="emisores",
        verbose_name="tipo de identificación",
    )
    numero_identificacion = models.CharField(
        "número de identificación", max_length=20,
        help_text="NIT sin puntos, sin guiones y sin dígito de verificación.",
    )
    digito_verificacion = models.CharField(
        "dígito de verificación", max_length=1, blank=True
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

    # Ubicación
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
    direccion = models.CharField("dirección", max_length=255)

    # Contacto
    telefono = models.CharField("teléfono", max_length=50, blank=True)
    correo = models.EmailField("correo electrónico", blank=True)

    activo = models.BooleanField("activo", default=True)

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


class SoftwareDian(ModeloConFechas):
    """Software de facturación registrado por el emisor ante la DIAN.

    El ``pin`` no se incluye en el XML; se usa para el CUDE y el
    ``SoftwareSecurityCode``.
    """

    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="softwares",
        verbose_name="emisor",
    )
    identificador = models.CharField(
        "ID del software", max_length=100,
        help_text="SoftwareID asignado por la DIAN.",
    )
    pin = models.CharField("PIN del software", max_length=100)
    id_proveedor = models.CharField(
        "ID del proveedor", max_length=20,
        help_text="ProviderID: NIT del proveedor del software (sin DV).",
    )
    test_set_id = models.CharField(
        "ID del set de pruebas", max_length=100, blank=True,
        help_text="TestSetId entregado por la DIAN para la habilitación.",
    )
    activo = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = "software DIAN"
        verbose_name_plural = "softwares DIAN"

    def __str__(self):
        return f"Software {self.identificador} ({self.emisor})"


class CertificadoDigital(ModeloConFechas):
    """Certificado digital (.p12/.pfx) del emisor para la firma XAdES.

    El archivo y la clave son sensibles: el .p12 está fuera del control de
    versiones (ver .gitignore) y la clave debería cifrarse en producción.
    """

    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="certificados",
        verbose_name="emisor",
    )
    alias = models.CharField("alias", max_length=150, blank=True)
    archivo = models.FileField("archivo .p12", upload_to="certificados/")
    clave = models.CharField("clave del certificado", max_length=255)
    vigente_desde = models.DateField("vigente desde", null=True, blank=True)
    vigente_hasta = models.DateField("vigente hasta", null=True, blank=True)
    activo = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = "certificado digital"
        verbose_name_plural = "certificados digitales"

    def __str__(self):
        return f"Certificado {self.alias or self.pk} ({self.emisor})"


class ResolucionFacturacion(ModeloConFechas):
    """Resolución de numeración (autorización de rango y clave técnica).

    La ``clave_tecnica`` es la que se usa para calcular el CUFE y NO viaja en
    el XML. Se obtiene de la consulta del rango de numeración ante la DIAN.
    """

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

    class Meta:
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
