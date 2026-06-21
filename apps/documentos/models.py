"""
Modelos de documentos electrónicos: adquirentes, documento electrónico
(factura, notas, documento soporte), sus líneas e impuestos.
"""
from django.db import models

from apps.nucleo.models import ModeloConFechas, ModeloUUID


class Adquirente(ModeloConFechas):
    """Cliente / receptor del documento (``cac:AccountingCustomerParty``)."""

    razon_social = models.CharField("razón social", max_length=450)
    tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion",
        on_delete=models.PROTECT,
        related_name="adquirentes",
        verbose_name="tipo de identificación",
    )
    numero_identificacion = models.CharField(
        "número de identificación", max_length=20,
        help_text="Sin puntos, sin guiones y sin dígito de verificación.",
    )
    digito_verificacion = models.CharField(
        "dígito de verificación", max_length=1, blank=True
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
    direccion = models.CharField("dirección", max_length=255, blank=True)
    telefono = models.CharField("teléfono", max_length=50, blank=True)
    correo = models.EmailField("correo electrónico", blank=True)

    class Meta:
        verbose_name = "adquirente"
        verbose_name_plural = "adquirentes"
        ordering = ["razon_social"]

    def __str__(self):
        return f"{self.numero_identificacion} - {self.razon_social}"


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


class LineaDocumento(ModeloConFechas):
    """Línea / ítem de un documento (``cac:InvoiceLine``)."""

    documento = models.ForeignKey(
        DocumentoElectronico, on_delete=models.CASCADE,
        related_name="lineas", verbose_name="documento",
    )
    numero_linea = models.PositiveIntegerField("número de línea")

    descripcion = models.CharField("descripción", max_length=500)
    codigo_producto = models.CharField("código del producto", max_length=100, blank=True)

    cantidad = models.DecimalField(
        "cantidad", max_digits=18, decimal_places=6, default=1
    )
    unidad_medida = models.ForeignKey(
        "catalogos.UnidadMedida", on_delete=models.PROTECT,
        related_name="lineas", verbose_name="unidad de medida",
    )
    valor_unitario = models.DecimalField(
        "valor unitario", max_digits=18, decimal_places=6, default=0
    )
    valor_total = models.DecimalField(
        "valor total de la línea", max_digits=18, decimal_places=2, default=0,
        help_text="cantidad × valor unitario (LineExtensionAmount).",
    )
    descuento = models.DecimalField(
        "descuento", max_digits=18, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = "línea de documento"
        verbose_name_plural = "líneas de documento"
        ordering = ["numero_linea"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "numero_linea"],
                name="linea_numero_unico_por_documento",
            )
        ]

    def __str__(self):
        return f"Línea {self.numero_linea} de {self.documento.numero}"


class ImpuestoLinea(ModeloConFechas):
    """Impuesto aplicado a una línea (``cac:TaxTotal`` dentro de la línea)."""

    linea = models.ForeignKey(
        LineaDocumento, on_delete=models.CASCADE,
        related_name="impuestos", verbose_name="línea",
    )
    tributo = models.ForeignKey(
        "catalogos.Tributo", on_delete=models.PROTECT,
        related_name="impuestos_linea", verbose_name="tributo",
    )
    base_gravable = models.DecimalField(
        "base gravable", max_digits=18, decimal_places=2, default=0
    )
    tarifa = models.DecimalField(
        "tarifa (%)", max_digits=7, decimal_places=4, default=0
    )
    valor = models.DecimalField(
        "valor del impuesto", max_digits=18, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = "impuesto de línea"
        verbose_name_plural = "impuestos de línea"

    def __str__(self):
        return f"{self.tributo} {self.tarifa}% = {self.valor}"
