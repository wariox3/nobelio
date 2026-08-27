"""Tipo de documento electrónico (discriminador interno + InvoiceTypeCode DIAN)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class DocumentoTipo(ModeloConFechas):
    """Tipo de documento electrónico.

    ``codigo`` es el discriminador interno que selecciona la lógica de
    generación (constructor UBL); ``codigo_dian`` es el InvoiceTypeCode oficial.
    """

    class Codigo(models.TextChoices):
        FACTURA_VENTA = "factura_venta", "Factura de venta"
        NOTA_CREDITO = "nota_credito", "Nota crédito"
        NOTA_DEBITO = "nota_debito", "Nota débito"
        DOCUMENTO_SOPORTE = "documento_soporte", "Documento soporte"
        NOTA_AJUSTE = "nota_ajuste", "Nota de ajuste al documento soporte"
        NOMINA = "nomina", "Nómina electrónica"

    # Los tipos que se numeran con una resolución autorizada por la DIAN: de
    # ella sale el bloque sts:InvoiceControl del XML, y en la factura además la
    # clave técnica del CUFE. El documento soporte tiene numeración propia,
    # distinta de la de facturación (anexo DS, nota-1 del numeral 14.1.1.2); su
    # CUDS no usa clave técnica sino el PIN del software, así que su resolución
    # puede no traerla. Las notas heredan la numeración del documento que
    # corrigen y no llevan InvoiceControl.
    CODIGOS_CON_RESOLUCION = frozenset({
        Codigo.FACTURA_VENTA,
        Codigo.DOCUMENTO_SOPORTE,
    })

    # Los tipos que llevan las retenciones aparte: no suman al total a pagar y
    # en el XML salen en cac:WithholdingTaxTotal (ver `emite_retenciones` en
    # `apps/dian/ubl.py`). Solo el régimen del documento soporte: el anexo de
    # factura no usa ese elemento, y descontarlas allí del total dejaría el
    # TaxInclusiveAmount sin cuadrar con los cac:TaxTotal que la factura sí
    # emite.
    CODIGOS_CON_RETENCIONES = frozenset({
        Codigo.DOCUMENTO_SOPORTE,
        Codigo.NOTA_AJUSTE,
    })

    # Los tipos que corrigen otro documento: su XML se construye con
    # cac:DiscrepancyResponse y cac:BillingReference, así que sin el documento
    # referenciado no hay nada que emitir (ver `_ConstructorNotaUBL`).
    CODIGOS_CON_REFERENCIA = frozenset({
        Codigo.NOTA_CREDITO,
        Codigo.NOTA_DEBITO,
        Codigo.NOTA_AJUSTE,
    })

    # Los tipos en los que el `adquiriente` del documento no es el receptor sino
    # el **vendedor** no obligado a facturar (SNO), y el que emite y firma es el
    # comprador. De ahí salen los roles UBL invertidos y el CustomizationID por
    # procedencia del vendedor.
    CODIGOS_CON_VENDEDOR_NO_OBLIGADO = frozenset({
        Codigo.DOCUMENTO_SOPORTE,
        Codigo.NOTA_AJUSTE,
    })

    codigo = models.CharField(
        "código", max_length=30, unique=True, choices=Codigo.choices,
        help_text="Discriminador interno que define la lógica de generación.",
    )
    nombre = models.CharField("nombre", max_length=100)
    codigo_dian = models.CharField(
        "código DIAN (InvoiceTypeCode)", max_length=2, blank=True,
        help_text="InvoiceTypeCode oficial (01, 02, 91, 92, 05).",
    )
    activo = models.BooleanField("activo", default=True)

    class Meta:
        db_table = "doc_documento_tipo"
        verbose_name = "tipo de documento"
        verbose_name_plural = "tipos de documento"
        ordering = ["codigo"]

    def __str__(self):
        return self.nombre
