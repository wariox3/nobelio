"""Catálogos geográficos (país, departamento, municipio)."""
from django.db import models

from .base import ElementoCatalogo


class Pais(ElementoCatalogo):
    """País (cac:Country), ISO 3166. Lista Paises."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_pais"
        verbose_name = "país"
        verbose_name_plural = "países"


class Departamento(ElementoCatalogo):
    """Departamento de Colombia (DANE, 2 dígitos). Lista Departamentos."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_departamento"
        verbose_name = "departamento"
        verbose_name_plural = "departamentos"


class Municipio(ElementoCatalogo):
    """Municipio de Colombia (DANE, 5 dígitos). Lista Municipio.

    El departamento se deriva de los dos primeros dígitos del código.
    """

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
