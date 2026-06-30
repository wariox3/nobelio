"""Estado interno del ciclo de vida de un documento electrónico."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class DocumentoEstado(ModeloConFechas):
    """Catálogo de estados internos por los que pasa un documento.

    Es el ciclo de vida propio del sistema (borrador → … → aceptado/rechazado),
    distinto del código de respuesta de la DIAN.
    """

    class Codigo(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        GENERADO = "generado", "XML generado"
        FIRMADO = "firmado", "Firmado"
        ENVIADO = "enviado", "Enviado a la DIAN"
        ACEPTADO = "aceptado", "Aceptado por la DIAN"
        RECHAZADO = "rechazado", "Rechazado por la DIAN"

    codigo = models.CharField(
        "código", max_length=20, unique=True, choices=Codigo.choices,
    )
    descripcion = models.CharField("descripción", max_length=100)
    activo = models.BooleanField("activo", default=True)

    class Meta:
        db_table = "doc_documento_estado"
        verbose_name = "estado de documento"
        verbose_name_plural = "estados de documento"
        ordering = ["id"]

    def __str__(self):
        return self.descripcion
