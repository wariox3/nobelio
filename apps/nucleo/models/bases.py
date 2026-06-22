"""Modelos base reutilizables por las demás apps."""
import uuid

from django.db import models


class ModeloConFechas(models.Model):
    """Modelo abstracto con marcas de tiempo de creación y actualización."""

    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        abstract = True


class ModeloUUID(models.Model):
    """Modelo abstracto con llave primaria UUID (para recursos expuestos por API)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True
