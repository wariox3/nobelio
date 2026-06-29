"""Documento electrónico: factura de venta, notas, documento soporte."""
from django.db import models

from apps.nucleo.models import ModeloConFechas, ModeloUUID

from .adquiriente import Adquiriente


class Documento(ModeloUUID, ModeloConFechas):
    """Documento electrónico (factura de venta, notas, documento soporte).

    Las notas (crédito/débito) referencian el documento corregido vía
    ``documento_referencia``.
    """

    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        GENERADO = "generado", "XML generado"
        FIRMADO = "firmado", "Firmado"
        ENVIADO = "enviado", "Enviado a la DIAN"
        ACEPTADO = "aceptado", "Aceptado por la DIAN"
        RECHAZADO = "rechazado", "Rechazado por la DIAN"

    # ===================== Atributos =====================
    estado = models.CharField(
        "estado", max_length=20, choices=Estado.choices, default=Estado.BORRADOR
    )

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

    # Artefactos generados
    xml_firmado = models.TextField("XML firmado", blank=True)
    respuesta_dian = models.TextField("respuesta DIAN", blank=True)
    track_id = models.CharField(
        "track id DIAN", max_length=100, blank=True,
        help_text="Identificador del envío (ZipKey del Set de Pruebas o trackId), "
        "para consultar el estado en la DIAN.",
    )

    # ===================== Relaciones =====================
    documento_tipo = models.ForeignKey(
        "documentos.DocumentoTipo", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="tipo de documento",
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

    # Origen del documento (trazabilidad): qué integración (ERP) o qué usuario
    # del frontend lo creó. Se rellenan según cómo entró la petición.
    origen_llave = models.ForeignKey(
        "seguridad.LlaveApi", on_delete=models.SET_NULL,
        related_name="documentos", null=True, blank=True,
        verbose_name="origen (llave de API)",
    )
    origen_usuario = models.ForeignKey(
        "seguridad.Usuario", on_delete=models.SET_NULL,
        related_name="documentos_emitidos", null=True, blank=True,
        verbose_name="origen (usuario)",
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
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.documento_tipo.nombre} {self.numero}"
