"""Catálogo: festivos de Colombia."""
from django.db import models

from .base import ElementoCatalogo


class Festivo(ElementoCatalogo):
    """Un festivo nacional, para contar días hábiles (``calendario.py``).

    No es una lista de la DIAN: es propia y se mantiene a mano cada año, en
    ``datos/listas/calendario/Festivo.gc``. El código es la fecha
    (``2026-01-12``) y el id, la fecha como número (``20260112``); la carga
    copia el código en ``fecha``.
    """

    fecha = models.DateField("fecha", unique=True)

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_festivo"
        verbose_name = "festivo"
        verbose_name_plural = "festivos"
