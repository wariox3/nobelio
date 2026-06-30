"""Error/notificación devuelto por la DIAN para un documento."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .documento import Documento


class DocumentoError(ModeloConFechas):
    """Un error o notificación de validación de la DIAN sobre un documento.

    La DIAN devuelve cadenas como ``Regla: FAJ24, Rechazo: <mensaje>``; aquí se
    guardan parseadas para poder consultar/filtrar (por regla o por tipo).
    """

    class Tipo(models.TextChoices):
        RECHAZO = "rechazo", "Rechazo"
        NOTIFICACION = "notificacion", "Notificación"
        OTRO = "otro", "Otro"

    # --- Atributos ---
    regla = models.CharField("regla", max_length=20, blank=True)
    tipo = models.CharField(
        "tipo", max_length=20, choices=Tipo.choices, default=Tipo.OTRO
    )
    mensaje = models.TextField("mensaje")

    # --- Relaciones ---
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name="errores", verbose_name="documento",
    )

    class Meta:
        db_table = "doc_documento_error"
        verbose_name = "error de documento"
        verbose_name_plural = "errores de documento"
        ordering = ["id"]

    def __str__(self):
        return f"{self.regla or self.tipo}: {self.mensaje[:60]}"
