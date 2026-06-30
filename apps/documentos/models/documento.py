"""Documento electrónico: factura de venta, notas, documento soporte."""
from django.db import models

from apps.nucleo.models import ModeloConFechas, ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze

from .adquiriente import Adquiriente


def _ruta_artefacto(instance, filename):
    """Ruta en el bucket: ``<emisor_id>/documentos/<aaaa>/<mm>/<archivo>``.

    Aísla por emisor (cada emisor pertenece a una cuenta) y agrupa por
    año/mes para mantener manejable el número de objetos por carpeta.
    """
    fecha = instance.fecha_emision
    return f"{instance.emisor_id}/documentos/{fecha:%Y/%m}/{filename}"


class Documento(ModeloUUID, ModeloConFechas):
    """Documento electrónico (factura de venta, notas, documento soporte).

    Las notas (crédito/débito) referencian el documento corregido vía
    ``documento_referencia``.
    """

    # ===================== Atributos =====================
    # Identificación del documento
    prefijo = models.CharField("prefijo", max_length=10, blank=True)
    consecutivo = models.PositiveBigIntegerField("consecutivo")
    numero = models.CharField(
        "número", max_length=30, blank=True,
        help_text="Número del documento (cbc:ID). Si se omite, se arma como "
        "prefijo + consecutivo.",
    )

    # Identificadores DIAN
    cufe_cude = models.CharField(
        "CUFE/CUDE", max_length=96, blank=True,
        help_text="Hash SHA-384 (96 hex). CUFE en facturas, CUDE en notas/soporte.",
    )

    fecha_emision = models.DateField("fecha de emisión")
    hora_emision = models.TimeField("hora de emisión")

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

    observaciones = models.TextField("observaciones", blank=True)

    # Resultado de la DIAN
    track_id = models.CharField(
        "track id DIAN", max_length=100, blank=True,
        help_text="Identificador del envío (ZipKey del Set de Pruebas o trackId), "
        "para consultar el estado en la DIAN.",
    )

    # Artefactos en object storage (B2): el contenido NO se guarda en la BD.
    xml_archivo = models.FileField(
        "XML firmado", upload_to=_ruta_artefacto,
        storage=almacenamiento_backblaze, blank=True,
    )
    respuesta_archivo = models.FileField(
        "respuesta DIAN (cruda)", upload_to=_ruta_artefacto,
        storage=almacenamiento_backblaze, blank=True,
    )

    # ===================== Relaciones =====================
    documento_tipo = models.ForeignKey(
        "documentos.DocumentoTipo", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="tipo de documento",
    )
    estado = models.ForeignKey(
        "documentos.DocumentoEstado", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="estado",
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
    adquiriente = models.ForeignKey(
        Adquiriente, on_delete=models.PROTECT,
        related_name="documentos", verbose_name="adquiriente",
    )
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
    # Para notas crédito/débito: referencia al documento corregido.
    documento_referencia = models.ForeignKey(
        "self", on_delete=models.PROTECT,
        related_name="notas", null=True, blank=True,
        verbose_name="documento de referencia",
    )

    class Meta:
        db_table = "doc_documento"
        verbose_name = "documento electrónico"
        verbose_name_plural = "documentos electrónicos"
        ordering = ["-fecha_emision", "-consecutivo"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "prefijo", "consecutivo", "documento_tipo"],
                name="documento_numero_unico_por_emisor",
            )
        ]

    def save(self, *args, **kwargs):
        # El número es la composición prefijo + consecutivo salvo que se fije
        # explícitamente (p. ej. para reproducir un número específico).
        if not self.numero:
            self.numero = f"{self.prefijo}{self.consecutivo}"
        # Estado inicial por defecto: borrador.
        if not self.estado_id:
            from .documento_estado import DocumentoEstado
            self.estado = DocumentoEstado.objects.get(
                codigo=DocumentoEstado.Codigo.BORRADOR
            )
        super().save(*args, **kwargs)

    def leer_xml(self) -> bytes:
        """Devuelve los bytes del XML firmado desde el storage (B2/local)."""
        if not self.xml_archivo:
            return b""
        with self.xml_archivo.open("rb") as fh:
            return fh.read()

    def __str__(self):
        return f"{self.documento_tipo.nombre} {self.numero}"
