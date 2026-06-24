"""Cuenta: el cliente/tenant de la plataforma.

Representa a quien usa el servicio (se integra desde su ERP o como externo).
Es un concepto de negocio propio, distinto del Emisor (la entidad legal que
factura ante la DIAN). Una cuenta puede agrupar varios emisores (NITs).
"""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class Cuenta(ModeloConFechas):
    """Cliente/tenant de la plataforma. Agrupa uno o varios emisores."""

    # --- Atributos ---
    nombre = models.CharField("nombre", max_length=255)
    identificacion = models.CharField("identificación", max_length=20, blank=True, help_text="NIT o documento del cliente (opcional, sin DV).",)
    correo_contacto = models.EmailField("correo de contacto", blank=True)
    activa = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "cuenta"
        verbose_name_plural = "cuentas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
