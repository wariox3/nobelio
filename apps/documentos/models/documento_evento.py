"""Registro de los cambios de estado de los documentos."""
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .documento import Documento


class DocumentoEvento(models.Model):
    """Un cambio de estado de un documento ante la DIAN.

    Solo los cambios de estado: firmar, quedar enviado sin veredicto, validarse
    o rechazarse. Crear no deja evento —la fecha de creación ya está en el
    documento— ni lo deja una consulta que no cambia nada, un fallo de red o una
    notificación. Las filas solo se crean: nadie las edita.

    La nómina tiene el suyo, ``NominaEvento``, con la misma forma.
    """

    class Tipo(models.TextChoices):
        FIRMADO = "firmado", "Firmado"
        ENVIADO = "enviado", "Enviado sin veredicto"
        VALIDADO = "validado", "Validado por la DIAN"
        RECHAZADO = "rechazado", "Rechazado por la DIAN"

    tipo = models.CharField("tipo", max_length=20, choices=Tipo.choices)
    # El detalle depende del tipo: el CUFE al firmar; al enviar o validarse, la
    # operación, el track_id y el código de la DIAN; al rechazarse, además los
    # errores. El encoder es para las fechas.
    datos = models.JSONField("datos", default=dict, blank=True, encoder=DjangoJSONEncoder)
    fecha = models.DateTimeField("fecha", auto_now_add=True)

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name="eventos", verbose_name="documento",
    )

    class Meta:
        db_table = "doc_documento_evento"
        verbose_name = "evento de documento"
        verbose_name_plural = "eventos de documento"
        ordering = ["fecha", "id"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.documento.numero}"
