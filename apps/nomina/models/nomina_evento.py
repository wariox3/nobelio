"""Registro de los cambios de estado de las nóminas."""
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class NominaEvento(models.Model):
    """Un cambio de estado de una nómina ante la DIAN.

    Solo los cambios de estado: firmar, quedar enviada sin veredicto, validarse
    o rechazarse. Crear no deja evento, ni una consulta que no cambia nada ni un
    fallo de red. Las filas solo se crean: nadie las edita.

    El documento tiene el suyo, ``DocumentoEvento``, con la misma forma y los
    mismos tipos: los registra el mismo servicio.
    """

    class Tipo(models.TextChoices):
        FIRMADO = "firmado", "Firmado"
        ENVIADO = "enviado", "Enviado sin veredicto"
        VALIDADO = "validado", "Validado por la DIAN"
        RECHAZADO = "rechazado", "Rechazado por la DIAN"

    tipo = models.CharField("tipo", max_length=20, choices=Tipo.choices)
    # El detalle depende del tipo: el CUNE al firmar; al enviar o validarse, la
    # operación, el track_id y el código de la DIAN; al rechazarse, además los
    # errores. El encoder es para las fechas.
    datos = models.JSONField("datos", default=dict, blank=True, encoder=DjangoJSONEncoder)
    fecha = models.DateTimeField("fecha", auto_now_add=True)

    nomina = models.ForeignKey(
        "nomina.Nomina", on_delete=models.CASCADE,
        related_name="eventos", verbose_name="nómina",
    )

    class Meta:
        db_table = "nom_nomina_evento"
        verbose_name = "evento de nómina"
        verbose_name_plural = "eventos de nómina"
        ordering = ["fecha", "id"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.nomina.numero}"
