"""
Modelos de catálogos DIAN.

Almacenan en base de datos las listas de valores oficiales (formato Genericode,
ver ``genericode.py``) para que los emisores y documentos puedan referenciarlas
con integridad referencial. Se cargan con ``python manage.py cargar_catalogos``.
"""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class ElementoCatalogo(ModeloConFechas):
    """Base abstracta para una entrada de catálogo (código + nombre)."""

    codigo = models.CharField("código", max_length=20, unique=True)
    nombre = models.CharField("nombre", max_length=255)
    activo = models.BooleanField("activo", default=True)

    class Meta:
        abstract = True
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class TipoFactura(ElementoCatalogo):
    """Tipo de documento / factura (cbc:InvoiceTypeCode). Lista TipoDocumento."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tipo de factura"
        verbose_name_plural = "tipos de factura"


class TipoIdentificacion(ElementoCatalogo):
    """Tipo de identificación fiscal (cédula, NIT, etc.). Lista TipoIdFiscal."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tipo de identificación"
        verbose_name_plural = "tipos de identificación"


class TipoOrganizacion(ElementoCatalogo):
    """Tipo de organización (persona natural / jurídica). Lista TipoOrganizacion."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tipo de organización"
        verbose_name_plural = "tipos de organización"


class ResponsabilidadFiscal(ElementoCatalogo):
    """Responsabilidad tributaria (cbc:TaxLevelCode). Lista TipoResponsabilidad.

    El código puede ser alfanumérico (p. ej. ``O-13``, ``R-99-PN``).
    """

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "responsabilidad fiscal"
        verbose_name_plural = "responsabilidades fiscales"


class Tributo(ElementoCatalogo):
    """Tributo / impuesto (cac:TaxScheme). Lista TipoImpuesto.

    Códigos relevantes para el CUFE: 01 IVA, 03 ICA, 04 INC.
    """

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tributo"
        verbose_name_plural = "tributos"


class UnidadMedida(ElementoCatalogo):
    """Unidad de medida (cbc:unitCode). Lista UnidadesMedida."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "unidad de medida"
        verbose_name_plural = "unidades de medida"


class FormaPago(ElementoCatalogo):
    """Forma de pago: contado / crédito (cbc:PaymentMeansCode). Lista FormasPago."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "forma de pago"
        verbose_name_plural = "formas de pago"


class MedioPago(ElementoCatalogo):
    """Medio de pago: efectivo, transferencia, etc. Lista MediosPago."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "medio de pago"
        verbose_name_plural = "medios de pago"


class Moneda(ElementoCatalogo):
    """Moneda (cbc:DocumentCurrencyCode), ISO 4217. Lista TipoMoneda."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "moneda"
        verbose_name_plural = "monedas"


class Pais(ElementoCatalogo):
    """País (cac:Country), ISO 3166. Lista Paises."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "país"
        verbose_name_plural = "países"


class Departamento(ElementoCatalogo):
    """Departamento de Colombia (DANE, 2 dígitos). Lista Departamentos."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "departamento"
        verbose_name_plural = "departamentos"


class Municipio(ElementoCatalogo):
    """Municipio de Colombia (DANE, 5 dígitos). Lista Municipio.

    El departamento se deriva de los dos primeros dígitos del código.
    """

    departamento = models.ForeignKey(
        Departamento,
        on_delete=models.PROTECT,
        related_name="municipios",
        null=True,
        blank=True,
        verbose_name="departamento",
    )

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "municipio"
        verbose_name_plural = "municipios"


class ConceptoNotaCredito(ElementoCatalogo):
    """Concepto de corrección de una nota crédito. Lista ConceptoNotaCredito."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "concepto de nota crédito"
        verbose_name_plural = "conceptos de nota crédito"


class ConceptoNotaDebito(ElementoCatalogo):
    """Concepto de corrección de una nota débito. Lista ConceptoNotaDebito."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "concepto de nota débito"
        verbose_name_plural = "conceptos de nota débito"
