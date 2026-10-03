"""Correo recibido en el buzón de recepción de un emisor."""
from django.db import models
from django.utils import timezone


class Correo(models.Model):
    """Un correo que llegó a ``<alias>@recepcion.rededoc.co``.

    Se crea `pendiente` en el request del Email Worker, sin parsear nada: el
    MIME se procesa después en el worker de Celery. El emisor se resuelve ahí
    (por alias y luego por el NIT receptor del XML), por eso nace nulo.

    El MIME crudo no vive aquí sino en R2, bajo ``raw_key``: es la fuente para
    procesarlo y para reprocesarlo.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PROCESADO = "procesado", "Procesado"
        SIN_DOCUMENTOS = "sin_documentos", "Sin documentos"
        ERROR = "error", "Error"
        EMPRESA_DESCONOCIDA = "empresa_desconocida", "Empresa desconocida"
        CONFIRMACION_REENVIO = "confirmacion_reenvio", "Confirmación de reenvío"

    # La parte local de X-Envelope-To, tal como llegó. Se guarda aunque no
    # corresponda a ningún emisor: así se puede reprocesar cuando lo registren.
    alias = models.CharField("alias", max_length=64, db_index=True)
    # SHA-256 del MIME crudo: la idempotencia del endpoint. El Worker puede
    # reintentar el POST, y el mismo correo no debe procesarse dos veces.
    sha256 = models.CharField("SHA-256", max_length=64, unique=True)
    raw_key = models.CharField("clave en R2", max_length=255)
    envelope_from = models.CharField("remitente (sobre)", max_length=320, blank=True)
    envelope_to = models.CharField("destinatario (sobre)", max_length=320)
    # Las cabeceras se llenan al procesar, no en el request.
    message_id = models.CharField("Message-ID", max_length=998, blank=True)
    asunto = models.CharField("asunto", max_length=998, blank=True)
    recibido_en = models.DateTimeField("recibido en", default=timezone.now)
    estado = models.CharField(
        "estado", max_length=30, choices=Estado.choices,
        default=Estado.PENDIENTE, db_index=True,
    )
    error_detalle = models.TextField("detalle del error", blank=True)
    intentos = models.PositiveSmallIntegerField("intentos", default=0)
    # El código o enlace que manda Gmail al configurar el reenvío
    # (forwarding-noreply@google.com): la empresa lo necesita para confirmarlo.
    confirmacion_reenvio = models.TextField("confirmación de reenvío", blank=True)

    # PROTECT por lo mismo que en los documentos: de aquí cuelgan documentos
    # fiscales de terceros.
    emisor = models.ForeignKey(
        "emisores.Emisor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correos_recibidos",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "rec_correo"
        verbose_name = "correo recibido"
        verbose_name_plural = "correos recibidos"
        ordering = ["-recibido_en", "-id"]

    def __str__(self):
        return f"{self.envelope_to} ({self.estado})"
