"""Evento RADIAN que el emisor registra sobre una factura recibida."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nucleo.models import Ambiente, ModeloConFechas, ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze


class EstadoEvento(models.TextChoices):
    """Dónde va el evento ante la DIAN."""

    # Creado y en la cola, o esperando que se reenvíe tras un error.
    PENDIENTE = "pendiente", "Pendiente"
    # La DIAN lo validó y lo registró en RADIAN.
    REGISTRADO = "registrado", "Registrado"
    # La DIAN lo rechazó (reglas, repetido, fuera de plazo). Es definitivo:
    # para intentarlo de nuevo se crea otro evento.
    RECHAZADO = "rechazado", "Rechazado"
    # No se pudo enviar (certificado, red, la DIAN no respondió). No se sabe si
    # la DIAN lo tiene: se reenvía con `enviar/`, con el mismo XML.
    ERROR = "error", "Error al enviar"


def _ruta(instance, filename):
    """``<emisor>/recepcion/eventos/<aaaa>/<mm>/<uuid>.xml``."""
    return f"{instance.emisor_id}/recepcion/eventos/{timezone.now():%Y/%m}/{uuid.uuid4()}.xml"


class Evento(ModeloUUID, ModeloConFechas):
    """Un evento RADIAN del adquiriente (030 a 033) sobre un ``Documento``.

    Lo emite el emisor que recibió la factura y se le dirige al proveedor que
    la emitió. El XML se genera, se firma y se envía en segundo plano
    (``apps.recepcion.eventos``): al crearse solo lleva lo que pidió el usuario
    y su número.

    El número es un consecutivo por emisor y tipo de evento, con un prefijo
    por código (``ACR1``, ``REC1``...): la DIAN rechaza un ``cbc:ID`` repetido
    para el mismo tipo de evento del mismo emisor.
    """

    numero = models.CharField("número", max_length=30, help_text="cbc:ID del evento.")
    consecutivo = models.PositiveIntegerField("consecutivo")
    estado = models.CharField(
        "estado", max_length=20, choices=EstadoEvento.choices,
        default=EstadoEvento.PENDIENTE, db_index=True,
    )
    ambiente = models.PositiveSmallIntegerField("ambiente", choices=Ambiente.choices)

    # Se llenan al generar el XML. La fecha y la hora son las de la firma, que
    # es contra lo que la DIAN cuenta los plazos.
    cude = models.CharField("CUDE", max_length=96, blank=True)
    fecha = models.DateField("fecha", null=True, blank=True)
    hora = models.TimeField("hora", null=True, blank=True)

    # La persona que recibió la factura o la mercancía (030 y 032).
    persona_tipo_identificacion = models.ForeignKey(
        "catalogos.TipoIdentificacion", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", verbose_name="tipo de identificación de quien recibe",
    )
    persona_numero_identificacion = models.CharField(
        "identificación de quien recibe", max_length=20, blank=True,
    )
    persona_nombres = models.CharField("nombres de quien recibe", max_length=100, blank=True)
    persona_apellidos = models.CharField("apellidos de quien recibe", max_length=100, blank=True)
    persona_cargo = models.CharField("cargo de quien recibe", max_length=100, blank=True)
    persona_area = models.CharField("área de quien recibe", max_length=100, blank=True)

    # Lo que respondió la DIAN, o por qué no se pudo enviar.
    respuesta_codigo = models.CharField("código de la respuesta", max_length=10, blank=True)
    respuesta_descripcion = models.TextField("detalle de la respuesta", blank=True)
    enviado_en = models.DateTimeField("enviado en", null=True, blank=True)
    intentos = models.PositiveSmallIntegerField("intentos de envío", default=0)

    xml_archivo = models.FileField(
        "XML firmado", upload_to=_ruta, storage=almacenamiento_backblaze, blank=True,
    )
    respuesta_archivo = models.FileField(
        "ApplicationResponse de la DIAN", upload_to=_ruta,
        storage=almacenamiento_backblaze, blank=True,
    )

    evento_radian = models.ForeignKey(
        "catalogos.EventoRadian", on_delete=models.PROTECT,
        related_name="eventos", verbose_name="evento",
    )
    concepto_reclamo = models.ForeignKey(
        "catalogos.ConceptoReclamo", on_delete=models.PROTECT, null=True, blank=True,
        related_name="eventos", verbose_name="concepto del reclamo",
    )
    # PROTECT: se borran solo con el correo, por el servicio que borra también
    # sus archivos en B2 (`adjuntos.eliminar_correo`).
    documento = models.ForeignKey(
        "recepcion.Documento", on_delete=models.PROTECT,
        related_name="eventos", verbose_name="documento",
    )
    # El del documento, repetido para el alcance y la numeración.
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT,
        related_name="eventos_recepcion", verbose_name="emisor",
    )
    # Quien lo pidió. Nulo en el acuse automático.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="eventos_recepcion", verbose_name="usuario",
    )

    class Meta:
        db_table = "rec_evento"
        verbose_name = "evento RADIAN"
        verbose_name_plural = "eventos RADIAN"
        ordering = ["-creado_en", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "evento_radian", "consecutivo"],
                name="rec_evento_consecutivo_unico",
            ),
        ]
        indexes = [
            models.Index(fields=["documento", "evento_radian"], name="rec_evento_documento"),
        ]

    def __str__(self):
        return f"{self.numero} ({self.evento_radian_id}) sobre {self.documento_id}"


class ConsecutivoEvento(models.Model):
    """El último número que se dio a un tipo de evento de un emisor.

    Aparte de ``rec_evento`` para que los números no se reutilicen: un evento
    rechazado se puede eliminar, y si el consecutivo saliera del máximo de los
    que quedan, el siguiente repetiría el suyo. Lo escribe solo
    ``eventos.solicitar``, bajo el candado del emisor.
    """

    ultimo = models.PositiveIntegerField("último consecutivo", default=0)
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.CASCADE,
        related_name="consecutivos_evento", verbose_name="emisor",
    )
    evento_radian = models.ForeignKey(
        "catalogos.EventoRadian", on_delete=models.PROTECT,
        related_name="+", verbose_name="evento",
    )

    class Meta:
        db_table = "rec_consecutivo_evento"
        verbose_name = "consecutivo de evento RADIAN"
        verbose_name_plural = "consecutivos de eventos RADIAN"
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "evento_radian"], name="rec_consecutivo_evento_unico",
            ),
        ]

    def __str__(self):
        return f"{self.emisor_id} {self.evento_radian_id}: {self.ultimo}"
