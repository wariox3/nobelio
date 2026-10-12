"""Webhooks del emisor o de la cuenta que lo agrupa."""
from django.core.validators import URLValidator
from django.db import models

from apps.nucleo.models import ModeloConFechas
from apps.utilidades.cifrado import ClaveCifradaField

from .cuenta import Cuenta
from .emisor import Emisor

MENSAJE_URL_WEB = "Tiene que ser una URL http:// o https:// válida."

# Uno solo, compartido por el modelo y el serializer: así una URL mal formada y
# una `ftp://` reciben el mismo mensaje, y no dos a la vez.
validar_url_web = URLValidator(schemes=["http", "https"], message=MENSAJE_URL_WEB)


class Webhook(ModeloConFechas):
    """Una URL a la que se le avisa de lo que pasa con los documentos.

    Es de un emisor o de una cuenta, nunca de los dos (``Meta.constraints``).
    El de la cuenta vale para todos sus emisores, los de hoy y los que entren
    después: es la URL del ERP que los integra, registrada una sola vez. Se
    **suma** a los del emisor: un documento avisa a los dos.

    Puede haber varios. Las banderas dicen de qué se le avisa a cada uno: de
    la validación de la DIAN y de la notificación al adquiriente.
    """

    nombre = models.CharField("nombre", max_length=150)
    # HTTP y HTTPS. Por HTTP el aviso —CUFE, fecha de validación, id del
    # documento y del cliente— viaja en claro: la firma prueba que salió de
    # aquí y que nadie lo alteró, pero no lo oculta. El validador se suma al de
    # `URLField` para dejar fuera ftp, que ese sí admite.
    url = models.URLField("URL", max_length=500, validators=[validar_url_web])
    estado_validado = models.BooleanField("avisar la validación", default=False)
    estado_notificado = models.BooleanField("avisar la notificación", default=False)
    # Con el que se firman las peticiones, para que el emisor pueda comprobar
    # que el aviso sale de aquí. Lo pone el cliente y es opcional. Se guarda
    # cifrado con su propia clave; en Python se lee en claro, que es lo que
    # hace falta para firmar. El 512 es por el token Fernet, no por el secreto.
    secreto = ClaveCifradaField(
        "secreto", max_length=512, blank=True, llave="WEBHOOK_ENCRYPTION_KEY",
    )

    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="webhooks",
        verbose_name="emisor",
    )
    cuenta = models.ForeignKey(
        Cuenta,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="webhooks",
        verbose_name="cuenta",
    )

    class Meta:
        db_table = "emi_webhook"
        verbose_name = "webhook"
        verbose_name_plural = "webhooks"
        ordering = ["-creado_en"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(emisor__isnull=False, cuenta__isnull=True)
                    | models.Q(emisor__isnull=True, cuenta__isnull=False)
                ),
                name="emi_webhook_de_emisor_o_de_cuenta",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.emisor or self.cuenta})"

    @classmethod
    def de(cls, emisor):
        """Los webhooks que aplican a ``emisor``: los suyos y los de su cuenta."""
        filtro = models.Q(emisor=emisor)
        if emisor.cuenta_id:
            filtro |= models.Q(cuenta_id=emisor.cuenta_id)
        return cls.objects.filter(filtro)
