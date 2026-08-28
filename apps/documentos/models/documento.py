"""Documento electrónico: factura de venta, notas, documento soporte."""
from django.conf import settings
from django.db import models

from apps.nucleo.models import Ambiente as AmbienteDian, ModeloConFechas, ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze


def ambiente_por_defecto():
    """El ambiente DIAN configurado en el servidor.

    Ya no es el default del campo: el documento hereda el ambiente de su emisor
    (ver ``save``), que es quien sabe si está en habilitación o en producción, y
    para cada operación por separado. Se conserva porque las migraciones ya
    hechas la referencian por su ruta de import y borrarla las rompería.
    """
    return settings.DIAN_ENVIRONMENT


def fecha_por_defecto():
    """La fecha local al crear el documento.

    Es también la única que se puede firmar: la DIAN exige que la fecha de
    emisión coincida con la de la firma (regla FAD09), así que un documento que
    no se emite el mismo día que se crea hay que refecharlo antes.
    """
    from django.utils import timezone

    return timezone.localdate()


def hora_por_defecto():
    """La hora local al crear el documento.

    Es solo un valor de partida: al firmar, ``generar_y_firmar`` la reescribe
    con la hora de la firma, que es la que la DIAN espera ver en el IssueTime.
    Existe para que quien crea el documento no tenga que inventarse una hora.
    """
    from django.utils import timezone

    return timezone.localtime().time()


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

    # La misma lista que usa la nómina: los dos valores los define la DIAN.
    # Se expone aquí para que ``Documento.Ambiente`` siga siendo el nombre por
    # el que se le llama desde fuera.
    Ambiente = AmbienteDian

    class Envio(models.TextChoices):
        SET_PRUEBAS = "test_set", "Set de Pruebas (SendTestSetAsync)"
        SINCRONO = "bill_sync", "Síncrono (SendBillSync)"

    # Conceptos de corrección (cbc:ResponseCode del DiscrepancyResponse). Son
    # dos listas distintas de la DIAN, una por tipo de nota, y comparten los
    # códigos "1" a "4" con significados que no tienen nada que ver: por eso el
    # campo no lleva `choices` y la lista aplicable la impone el tipo.
    class ConceptoNotaCredito(models.TextChoices):
        DEVOLUCION = "1", "Devolución parcial de bienes o no aceptación parcial del servicio"
        ANULACION = "2", "Anulación de factura electrónica"
        REBAJA = "3", "Rebaja o descuento parcial o total"
        AJUSTE_PRECIO = "4", "Ajuste de precio"
        OTROS = "5", "Otros"

    class ConceptoNotaAjuste(models.TextChoices):
        """Lista ConceptoNotaAjuste del anexo DS: redacción propia, no la de la nota crédito."""

        DEVOLUCION = "1", ("Devolución parcial de los bienes y/o no aceptación "
                           "parcial del servicio")
        ANULACION = "2", "Anulación del documento soporte"
        REBAJA = "3", "Rebaja o descuento parcial o total"
        AJUSTE_PRECIO = "4", "Ajuste de precio"
        OTROS = "5", "Otros"

    class ConceptoNotaDebito(models.TextChoices):
        INTERESES = "1", "Intereses"
        GASTOS = "2", "Gastos por cobrar"
        CAMBIO_VALOR = "3", "Cambio del valor"
        OTROS = "4", "Otros"

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
    ambiente = models.PositiveSmallIntegerField(
        "ambiente DIAN", choices=Ambiente.choices,
        help_text="ProfileExecutionID del XML: 1 producción, 2 habilitación. "
        "Se hereda del emisor al crear y se sella al firmar; no lo decide el "
        "ajuste del servidor.",
    )
    cufe_cude = models.CharField(
        "CUFE/CUDE", max_length=96, blank=True,
        help_text="Hash SHA-384 (96 hex). CUFE en facturas, CUDE en notas/soporte.",
    )

    fecha_emision = models.DateField(
        "fecha de emisión", default=fecha_por_defecto,
        help_text="IssueDate del XML. Si se omite se toma la fecha de hoy, que "
        "es la única con la que se puede firmar (regla FAD09).",
    )
    fecha_vencimiento = models.DateField(
        "fecha de vencimiento", null=True, blank=True,
        help_text="DueDate del XML: hasta cuándo hay plazo para pagar. La DIAN "
        "la exige cuando la forma de pago es a crédito; en contado sobra.",
    )
    hora_emision = models.TimeField(
        "hora de emisión", default=hora_por_defecto,
        help_text="IssueTime del XML. Si se omite se toma la hora actual, y al "
        "firmar se reescribe con la hora de la firma.",
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
        "total descuentos", max_digits=18, decimal_places=2, default=0,
        help_text="Descuentos globales del documento (no los de línea).",
    )
    descuentos_motivo = models.CharField(
        "motivo de los descuentos", max_length=255, blank=True,
        help_text="cbc:AllowanceChargeReason del descuento global.",
    )
    total_cargos = models.DecimalField(
        "total cargos", max_digits=18, decimal_places=2, default=0
    )
    cargos_motivo = models.CharField(
        "motivo de los cargos", max_length=255, blank=True,
        help_text="cbc:AllowanceChargeReason del cargo global.",
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
    envio = models.CharField(
        "operación de envío", max_length=20, choices=Envio.choices, blank=True,
        help_text="Con qué operación se envió a la DIAN. Decide cómo se "
        "consulta después el estado: GetStatusZip para el Set de Pruebas "
        "(el track_id es un ZipKey) y GetStatus para el envío síncrono. "
        "Vacío mientras no se haya enviado.",
    )
    notificado = models.BooleanField(
        "notificado al adquiriente", default=False,
        help_text="Si ya se le entregó el documento al comprador. Lo marca la "
        "acción `notificar`; mientras el envío por correo no exista, significa "
        "que el paquete se armó y se entregó a quien lo pidió.",
    )
    fecha_validacion = models.DateTimeField(
        "fecha y hora de validación DIAN", null=True, blank=True,
        help_text="Momento en que la DIAN aceptó el documento.",
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
        "emisores.Resolucion", on_delete=models.PROTECT,
        related_name="documentos", verbose_name="resolución",
        null=True, blank=True,
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
    # Orden de compra del comprador (cac:OrderReference).
    orden_compra = models.CharField(
        "orden de compra", max_length=100, blank=True,
        help_text="Número de la orden de compra del adquiriente, si la hubo.",
    )
    orden_compra_fecha = models.DateField(
        "fecha de la orden de compra", null=True, blank=True,
    )
    orden_compra_tipo = models.CharField(
        "tipo de la orden de compra", max_length=20, blank=True,
        help_text="cbc:OrderTypeCode: qué clase de orden es (contrato, pedido…), "
        "según la codificación que use el comprador.",
    )
    orden_compra_documento = models.CharField(
        "documento de la orden de compra", max_length=100, blank=True,
        help_text="cac:DocumentReference/cbc:ID: el soporte de la orden "
        "(contrato, acuerdo marco) cuando es distinto del número de la orden.",
    )

    # Para notas crédito/débito: referencia al documento corregido.
    documento_referencia = models.ForeignKey(
        "self", on_delete=models.PROTECT,
        related_name="notas", null=True, blank=True,
        verbose_name="documento de referencia",
    )
    concepto_correccion = models.CharField(
        "concepto de corrección", max_length=2, blank=True,
        help_text="ResponseCode del DiscrepancyResponse: por qué se corrige el "
        "documento referenciado. Los códigos válidos dependen del tipo de nota "
        "(ConceptoNotaCredito, ConceptoNotaDebito, ConceptoNotaAjuste). Vacío "
        "en lo que no es nota.",
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
        # El ambiente lo pone el emisor, no el ajuste global: puede haber unos
        # en habilitación y otros en producción a la vez. Se resuelve al crear
        # y el documento se lo queda, porque es lo que entra en el CUFE y lo
        # que decide a qué servidor se envía; si cambiara después, el
        # identificador y el destino dejarían de corresponderse. Un valor
        # explícito manda (lo usa la factura de prueba).
        if self.ambiente is None:
            self.ambiente = self.emisor.ambiente_facturacion
        # Estado inicial por defecto: borrador.
        if not self.estado_id:
            from .documento_estado import DocumentoEstado
            self.estado = DocumentoEstado.objects.get(
                nombre=DocumentoEstado.Nombre.BORRADOR
            )
        super().save(*args, **kwargs)

    @property
    def contraparte(self):
        """La otra parte del documento, se llame como se llame en cada tipo.

        En factura y notas el ``adquiriente`` es el receptor. En el documento
        soporte es el **vendedor** —el sujeto no obligado a facturar—, porque
        allí quien emite es el comprador. Los datos que hacen falta son los
        mismos, así que se guardan en la misma fila; este alias existe para que
        el código que trabaja con documento soporte no tenga que leer
        "adquiriente" queriendo decir lo contrario.
        """
        return self.adquiriente

    @property
    def es_borrador(self) -> bool:
        """Aún no se ha firmado, así que sus datos todavía se pueden cambiar."""
        from .documento_estado import DocumentoEstado

        if not self.estado_id:
            return True
        return self.estado.nombre in (
            DocumentoEstado.Nombre.BORRADOR, DocumentoEstado.Nombre.GENERADO,
        )

    def leer_xml(self) -> bytes:
        """Devuelve los bytes del XML firmado desde el storage (B2/local)."""
        if not self.xml_archivo:
            return b""
        with self.xml_archivo.open("rb") as fh:
            return fh.read()

    def __str__(self):
        return f"{self.documento_tipo.nombre} {self.numero}"
