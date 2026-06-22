"""Base abstracta de los catálogos DIAN."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class ElementoCatalogo(ModeloConFechas):
    """Base abstracta para una entrada de catálogo (código + nombre)."""

    codigo = models.CharField("código", max_length=20, unique=True)
    nombre = models.CharField("nombre", max_length=255)
    activo = models.BooleanField("activo", default=True)

    class Meta:
        abstract = True
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"
