"""Cuenta: el cliente/tenant de la plataforma.

Representa a quien usa el servicio (se integra desde su ERP o como externo).
Es un concepto de negocio propio, distinto del Emisor (la entidad legal que
factura ante la DIAN). Una cuenta puede agrupar varios emisores (NITs).
"""
from django.conf import settings
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Cuenta(ModeloConFechas):
    """Cliente/tenant de la plataforma. Agrupa uno o varios emisores."""

    # --- Atributos ---
    nombre = models.CharField("nombre", max_length=255)
    identificacion = models.CharField("identificación", max_length=20, blank=True, help_text="NIT o documento del cliente (opcional, sin DV).",)
    correo_contacto = models.EmailField("correo de contacto", blank=True)
    activa = models.BooleanField("activa", default=True)

    # --- Relaciones ---
    # Dueño de la cuenta. Quien se registra queda como el de la suya, y por ahí
    # gana lo que un usuario suelto no tiene: alcanzar los emisores de esa
    # cuenta y poder darlos de alta.
    #
    # Obligatorio: una cuenta sin dueño no la puede reclamar nadie y no hay
    # forma de avisarle a nadie de lo que pase con ella. El staff que abra una
    # a mano tiene que decir de quién es.
    #
    # PROTECT y no CASCADE: la cuenta guarda documentos fiscales de terceros,
    # así que borrar al dueño no puede llevarse por delante ese histórico. Para
    # dar de baja a alguien está `is_active`.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cuentas",
        verbose_name="usuario",
        help_text="Usuario dueño de la cuenta.",
    )

    class Meta:
        db_table = "cue_cuenta"
        verbose_name = "cuenta"
        verbose_name_plural = "cuentas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
