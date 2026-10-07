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

    def es_de(self, departamento):
        """¿Es el municipio de ese departamento?

        Manda la relación del catálogo. Como es nullable, cuando no está cargada
        se compara lo que la sustituye: el código DANE del municipio empieza por
        el del departamento (05001 es de 05, Antioquia).
        """
        if self.departamento_id is not None:
            return self.departamento_id == departamento.pk
        return self.codigo[:2] == departamento.codigo


def mensaje_municipio_de_otro_departamento(municipio, departamento):
    """Mensaje para un municipio que no es del departamento informado."""
    return (
        f"El municipio {municipio.nombre} ({municipio.codigo}) no pertenece al "
        f"departamento {departamento.nombre} ({departamento.codigo})."
    )
