"""Documento electrónico recibido de un proveedor."""
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Upper

from apps.nucleo.models import Ambiente, ModeloConFechas, ModeloUUID


class EstadoVerificacion(models.TextChoices):
    """Qué dijo la DIAN del documento al consultarlo por su CUFE."""

    PENDIENTE = "pendiente", "Pendiente"
    # La DIAN lo tiene registrado y válido.
    VALIDO = "valido", "Válido"
    # La DIAN no lo reconoce o lo tiene rechazado: no existe para ella.
    INVALIDO = "invalido", "No válido"
    # El emisor no tiene un certificado vigente con el que consultar.
    NO_VERIFICABLE = "no_verificable", "No verificable"
    # No se pudo consultar (la DIAN no respondió) y se agotaron los reintentos.
    ERROR = "error", "Error al consultar"


class Documento(ModeloUUID, ModeloConFechas):
    """Factura, nota crédito o nota débito que un proveedor le mandó a un emisor.

    Es una tabla aparte de ``doc_documento`` a propósito: allá el emisor es
    quien factura; aquí es quien **recibe**, y quien factura es un tercero que
    no está en la plataforma. Comparte con ella el catálogo de tipos, la moneda
    y los nombres de los campos que significan lo mismo.

    El CUFE es único: si el proveedor manda la misma factura dos veces, la
    segunda se ignora. Las filas solo las crea el procesamiento del correo.

    Sus archivos (el XML recibido, el XML del documento y el PDF) son
    ``Adjunto`` del correo con ``documento`` apuntando aquí.
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
    ambiente = models.PositiveSmallIntegerField(
        "ambiente", choices=Ambiente.choices, null=True, blank=True,
        help_text="ProfileExecutionID del XML. Vacío si no venía: se verifica en producción.",
    )

    # La verificación contra la DIAN (GetStatus por CUFE), que sí es de fiar:
    # la validación de arriba la escribe el proveedor en su AttachedDocument, y
    # un XML inventado puede traer cualquier cosa. Ver `apps.recepcion.verificacion`.
    verificacion_estado = models.CharField(
        "verificación DIAN", max_length=20, choices=EstadoVerificacion.choices,
        default=EstadoVerificacion.PENDIENTE,
    )
    verificacion_codigo = models.CharField(
        "código de la verificación", max_length=10, blank=True,
        help_text="StatusCode que respondió la DIAN.",
    )
    verificacion_descripcion = models.TextField(
        "detalle de la verificación", blank=True,
        help_text="StatusDescription y errores de la DIAN, o por qué no se pudo consultar.",
    )
    verificado_en = models.DateTimeField("verificado en", null=True, blank=True)

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
    # Sin índice propio: lo cubre `rec_doc_emisor_recientes`, que empieza por
    # el emisor. Uno más solo encarecería cada inserción.
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT, db_index=False,
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
        # El `-id` final desempata: sin él, paginar con OFFSET puede repetir o
        # saltarse filas que comparten fecha.
        ordering = ["-fecha_emision", "-creado_en", "-id"]
        indexes = [
            # La bandeja: los de un emisor, del más reciente al más viejo. Cubre
            # el orden completo para que Postgres no tenga que ordenar aparte.
            models.Index(
                fields=["emisor", "-fecha_emision", "-creado_en", "-id"],
                name="rec_doc_emisor_recientes",
            ),
            # La búsqueda (`?search=`). DRF la traduce a `UPPER(col) = / LIKE
            # UPPER(...)`, así que los índices van sobre `UPPER(col)`: uno
            # normal para la igualdad del CUFE y trigramas (`pg_trgm`) para
            # los LIKE de NIT, número y razón social, que con `%x%` no pueden
            # usar un B-tree.
            models.Index(Upper("cufe_cude"), name="rec_doc_cufe_upper"),
            GinIndex(
                OpClass(Upper("proveedor_numero_identificacion"), name="gin_trgm_ops"),
                name="rec_doc_nit_trgm",
            ),
            GinIndex(
                OpClass(Upper("numero"), name="gin_trgm_ops"),
                name="rec_doc_numero_trgm",
            ),
            GinIndex(
                OpClass(Upper("proveedor_razon_social"), name="gin_trgm_ops"),
                name="rec_doc_razon_social_trgm",
            ),
        ]

    def __str__(self):
        return f"{self.numero} de {self.proveedor_numero_identificacion}"
