"""Catálogo: municipio."""
from django.db import models

from .base import ElementoCatalogo
from .departamento import Departamento


class Municipio(ElementoCatalogo):
    """Municipio de Colombia (DANE, 5 dígitos). Lista Municipio.

    El departamento se deriva de los dos primeros dígitos del código.
    """

    codigo_postal = models.CharField(
        "código postal", max_length=6, blank=True,
        help_text="El de la cabecera. Respalda el cbc:PostalZone del XML "
        "cuando el emisor o el adquiriente no informan el suyo.",
    )

    departamento = models.ForeignKey(
        Departamento,
        on_delete=models.PROTECT,
        related_name="municipios",
        null=True,
        blank=True,
        verbose_name="departamento",
    )

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_municipio"
        verbose_name = "municipio"
        verbose_name_plural = "municipios"
