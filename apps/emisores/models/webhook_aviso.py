"""Los avisos que se mandan a los webhooks del emisor."""
from django.db import models

from .webhook import Webhook


class WebhookAviso(models.Model):
    """Un aviso a un webhook, con lo que respondió.

    Se crea `pendiente` en la misma transacción que el cambio que lo dispara
    —validarse el documento, notificarse—: si el cambio se deshace, el aviso
    también. Se envía una sola vez al confirmarse esa transacción, y el
    resultado queda aquí. Un aviso que se quede en `pendiente` es uno cuyo
    envío no llegó a terminar (el proceso se cayó a mitad).

    **No hay reintentos**, aunque el contrato con torio los pida para 401, 429,
    5xx y timeouts (`torio/docs/webhook_rededoc.md`, sección 3): fue decisión
    expresa. El reenvío, cuando exista, será manual; por eso se guarda el
    cuerpo exacto que se firmó, que es lo que habría que volver a mandar.
    """

    class Tipo(models.TextChoices):
        VALIDACION = "validacion", "Validación de la DIAN"
        NOTIFICACION = "notificacion", "Notificación al adquiriente"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ENTREGADO = "entregado", "Entregado"
        FALLIDO = "fallido", "Fallido"

    tipo = models.CharField("tipo", max_length=20, choices=Tipo.choices)
    estado = models.CharField(
        "estado", max_length=20, choices=Estado.choices, default=Estado.PENDIENTE,
    )
    # Los bytes exactos que se firman y se mandan, serializados una sola vez.
    # Volver a serializar el dict daría otro orden u otros espacios, y la firma
    # dejaría de cuadrar.
    cuerpo = models.TextField("cuerpo")
    codigo_http = models.PositiveSmallIntegerField("código HTTP", null=True, blank=True)
    # Por qué falló, o el `detail` de la respuesta. También en un 409, que
    # cuenta como entregado pero puede esconder un CUFE distinto del de torio.
    error = models.TextField("error", blank=True)
    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    enviado_en = models.DateTimeField("enviado en", null=True, blank=True)

    webhook = models.ForeignKey(
        Webhook, on_delete=models.CASCADE,
        related_name="avisos", verbose_name="webhook",
    )
    documento = models.ForeignKey(
        "documentos.Documento", on_delete=models.CASCADE,
        related_name="avisos_webhook", verbose_name="documento",
    )

    class Meta:
        db_table = "emi_webhook_aviso"
        verbose_name = "aviso de webhook"
        verbose_name_plural = "avisos de webhook"
        ordering = ["-creado_en", "-id"]

    def __str__(self):
        return f"{self.get_tipo_display()} → {self.webhook} ({self.estado})"
