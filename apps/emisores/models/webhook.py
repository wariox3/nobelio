"""Webhooks del emisor."""
from django.core.validators import URLValidator
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor

MENSAJE_SOLO_HTTPS = "Tiene que ser una URL HTTPS válida (https://…)."

# Uno solo, compartido por el modelo y el serializer: así una URL mal formada y
# una `http://` reciben el mismo mensaje, y no dos a la vez.
validar_url_https = URLValidator(schemes=["https"], message=MENSAJE_SOLO_HTTPS)


class Webhook(ModeloConFechas):
    """Una URL del emisor a la que se le avisa de lo que pasa con sus documentos.

    Un emisor puede tener varios. Las banderas dicen de qué se le avisa a cada
    uno: de la validación de la DIAN y de la notificación al adquiriente.
    """

    nombre = models.CharField("nombre", max_length=150)
    # Solo HTTPS: el aviso lleva datos fiscales del documento, y por HTTP
    # viajarían en claro. El validador se suma al de `URLField`, que admite
    # también http y ftp.
    url = models.URLField("URL", max_length=500, validators=[validar_url_https])
    estado_validado = models.BooleanField("avisar la validación", default=False)
    estado_notificado = models.BooleanField("avisar la notificación", default=False)

    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="webhooks",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "emi_webhook"
        verbose_name = "webhook"
        verbose_name_plural = "webhooks"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.nombre} ({self.emisor})"
