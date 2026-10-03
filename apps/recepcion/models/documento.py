"""Documento electrónico recibido de un proveedor."""
from django.db import models

from apps.nucleo.models import ModeloConFechas, ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze


def _ruta_archivo(instance, filename):
    """Ruta en el bucket: ``<emisor_id>/recepcion/<aaaa>/<mm>/<archivo>``.

    La misma forma que los artefactos de la emisión, en otra carpeta: aislado
    por emisor y agrupado por mes.
    """
    return f"{instance.emisor_id}/recepcion/{instance.fecha_emision:%Y/%m}/{filename}"


class Documento(ModeloUUID, ModeloConFechas):
    """Factura, nota crédito o nota débito que un proveedor le mandó a un emisor.

    Es una tabla aparte de ``doc_documento`` a propósito: allá el emisor es
    quien factura; aquí es quien **recibe**, y quien factura es un tercero que
    no está en la plataforma. Comparte con ella el catálogo de tipos, la moneda
    y los nombres de los campos que significan lo mismo.

    El CUFE es único: si el proveedor manda la misma factura dos veces, la
    segunda se ignora. Las filas solo las crea el procesamiento del correo.
    """

    # Identificación
    numero = models.CharField("número", max_length=30, help_text="cbc:ID del documento.")
    cufe_cude = models.CharField(
        "CUFE/CUDE", max_length=96, unique=True,
        help_text="cbc:UUID del documento. CUFE en facturas, CUDE en notas.",
    )
    fecha_emision = models.DateField("fecha de emisión")
    hora_emision = models.TimeField("hora de emisión", null=True, blank=True)
    tipo_codigo_dian = models.CharField(
        "código DIAN del tipo", max_length=2, blank=True,
        help_text="InvoiceTypeCode, CreditNoteTypeCode o DebitNoteTypeCode tal como vino.",
    )

    # Quien factura (el proveedor) y quien recibe (el NIT que trae el XML)
    proveedor_numero_identificacion = models.CharField(
        "NIT del proveedor", max_length=20, db_index=True,
    )
    proveedor_digito_verificacion = models.CharField(
        "DV del proveedor", max_length=1, blank=True,
    )
    proveedor_razon_social = models.CharField("razón social del proveedor", max_length=450, blank=True)
    receptor_numero_identificacion = models.CharField("NIT del receptor", max_length=20)

    # Valores
    valor_bruto = models.DecimalField(
        "valor bruto", max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="cbc:LineExtensionAmount.",
    )
    total_impuestos = models.DecimalField(
        "total impuestos", max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="Suma de los cbc:TaxAmount de los cac:TaxTotal del documento.",
    )
    total_a_pagar = models.DecimalField(
        "total a pagar", max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="cbc:PayableAmount.",
    )

    # La validación de la DIAN, si vino en el AttachedDocument
    validacion_codigo = models.CharField(
        "código de validación DIAN", max_length=10, blank=True,
        help_text="ValidationResultCode del AttachedDocument (02 = validado).",
    )
    fecha_validacion = models.DateTimeField("fecha de validación DIAN", null=True, blank=True)

    # Archivos en B2
    xml_archivo = models.FileField(
        "XML recibido", upload_to=_ruta_archivo, storage=almacenamiento_backblaze,
        help_text="El XML tal como llegó: el AttachedDocument o el documento suelto.",
    )
    xml_factura_archivo = models.FileField(
        "XML del documento", upload_to=_ruta_archivo,
        storage=almacenamiento_backblaze, blank=True,
        help_text="El documento extraído del AttachedDocument. Vacío si llegó suelto.",
    )
    pdf_archivo = models.FileField(
        "PDF", upload_to=_ruta_archivo, storage=almacenamiento_backblaze, blank=True,
    )

    # Relaciones
    documento_tipo = models.ForeignKey(
        "documentos.DocumentoTipo", on_delete=models.PROTECT,
        related_name="documentos_recibidos", verbose_name="tipo de documento",
    )
    moneda = models.ForeignKey(
        "catalogos.Moneda", on_delete=models.PROTECT, null=True, blank=True,
        related_name="documentos_recibidos", verbose_name="moneda",
    )
    # El emisor que lo recibe: el del NIT receptor del XML, que puede no ser el
    # del buzón al que llegó el correo.
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT,
        related_name="documentos_recibidos", verbose_name="emisor",
    )
    # PROTECT: un correo con documentos no se elimina (el DELETE de la API
    # solo borra correos sin emisor, que nunca los tienen).
    correo = models.ForeignKey(
        "recepcion.Correo", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="correo",
    )

    class Meta:
        db_table = "rec_documento"
        verbose_name = "documento recibido"
        verbose_name_plural = "documentos recibidos"
        ordering = ["-fecha_emision", "-creado_en"]
        indexes = [
            models.Index(fields=["emisor", "-fecha_emision"], name="rec_doc_emisor_recientes"),
        ]

    def __str__(self):
        return f"{self.numero} de {self.proveedor_numero_identificacion}"
