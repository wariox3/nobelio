"""Documento electrónico: factura de venta, notas, documento soporte."""
from django.db import models

from apps.nucleo.models import ModeloConFechas, ModeloUUID

from .adquirente import Adquirente


class DocumentoElectronico(ModeloUUID, ModeloConFechas):
    """Documento electrónico (factura de venta, notas, documento soporte).

    Las notas (crédito/débito) referencian el documento corregido vía
    ``documento_referencia``.
    """

    class Tipo(models.TextChoices):
        FACTURA_VENTA = "factura_venta", "Factura de venta"
        NOTA_CREDITO = "nota_credito", "Nota crédito"
        NOTA_DEBITO = "nota_debito", "Nota débito"
        DOCUMENTO_SOPORTE = "documento_soporte", "Documento soporte"
        NOMINA = "nomina", "Nómina electrónica"

    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        GENERADO = "generado", "XML generado"
        FIRMADO = "firmado", "Firmado"
        ENVIADO = "enviado", "Enviado a la DIAN"
        ACEPTADO = "aceptado", "Aceptado por la DIAN"
        RECHAZADO = "rechazado", "Rechazado por la DIAN"

    tipo = models.CharField("tipo", max_length=20, choices=Tipo.choices)
    estado = models.CharField(
        "estado", max_length=20, choices=Estado.choices, default=Estado.BORRADOR
    )

    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="emisor",
    )
    resolucion = models.ForeignKey(
        "emisores.ResolucionFacturacion", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="resolución",
        null=True, blank=True,
    )
    adquirente = models.ForeignKey(
        Adquirente, on_delete=models.PROTECT,
        related_name="documentos", verbose_name="adquirente",
    )

    # Identificación del documento
    prefijo = models.CharField("prefijo", max_length=10, blank=True)
    consecutivo = models.PositiveBigIntegerField("consecutivo")
    numero = models.CharField(
        "número", max_length=30,
        help_text="Prefijo concatenado con el consecutivo (cbc:ID).",
    )

    # Identificadores DIAN
    cufe_cude = models.CharField(
        "CUFE/CUDE", max_length=96, blank=True,
        help_text="Hash SHA-384 (96 hex). CUFE en facturas, CUDE en notas/soporte.",
    )

    fecha_emision = models.DateField("fecha de emisión")
    hora_emision = models.TimeField("hora de emisión")

    moneda = models.ForeignKey(
        "catalogos.Moneda", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="moneda",
    )
    forma_pago = models.ForeignKey(
        "catalogos.FormaPago", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="forma de pago",
        null=True, blank=True,
    )
    medio_pago = models.ForeignKey(
        "catalogos.MedioPago", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="medio de pago",
        null=True, blank=True,
    )

    # Totales (cac:LegalMonetaryTotal)
    valor_bruto = models.DecimalField(
        "valor bruto (sin impuestos)", max_digits=18, decimal_places=2, default=0,
        help_text="LineExtensionAmount: suma de las líneas.",
    )
    total_impuestos = models.DecimalField(
        "total impuestos", max_digits=18, decimal_places=2, default=0
    )
    total_descuentos = models.DecimalField(
        "total descuentos", max_digits=18, decimal_places=2, default=0
    )
    total_cargos = models.DecimalField(
        "total cargos", max_digits=18, decimal_places=2, default=0
    )
    total_a_pagar = models.DecimalField(
        "total a pagar", max_digits=18, decimal_places=2, default=0,
        help_text="PayableAmount.",
    )

    # Para notas crédito/débito
    documento_referencia = models.ForeignKey(
        "self", on_delete=models.PROTECT,
        related_name="notas", null=True, blank=True,
        verbose_name="documento de referencia",
    )

    observaciones = models.TextField("observaciones", blank=True)

    # Artefactos generados
    xml_firmado = models.TextField("XML firmado", blank=True)
    respuesta_dian = models.TextField("respuesta DIAN", blank=True)

    class Meta:
        verbose_name = "documento electrónico"
        verbose_name_plural = "documentos electrónicos"
        ordering = ["-fecha_emision", "-consecutivo"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "prefijo", "consecutivo", "tipo"],
                name="documento_numero_unico_por_emisor",
            )
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero}"
