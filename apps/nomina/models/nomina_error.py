"""Error o notificación devuelta por la DIAN para una nómina."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class NominaError(ModeloConFechas):
    """Un error de validación de la DIAN sobre una nómina.

    Igual que ``DocumentoError`` pero colgando de la nómina: la DIAN devuelve
    las mismas cadenas (``Regla: NIE021, Rechazo: <mensaje>``) y se parsean con
    el mismo código. No se reutiliza aquella tabla porque su FK apunta a
    ``Documento`` y la nómina no lo es.
    """

    class Tipo(models.TextChoices):
        RECHAZO = "rechazo", "Rechazo"
        NOTIFICACION = "notificacion", "Notificación"
        OTRO = "otro", "Otro"

    regla = models.CharField("regla", max_length=20, blank=True)
    tipo = models.CharField(
        "tipo", max_length=20, choices=Tipo.choices, default=Tipo.OTRO
    )
    mensaje = models.TextField("mensaje")

    nomina = models.ForeignKey(
        "nomina.Nomina", on_delete=models.CASCADE,
        related_name="errores", verbose_name="nómina",
    )

    class Meta:
        db_table = "nom_nomina_error"
        verbose_name = "error de nómina"
        verbose_name_plural = "errores de nómina"
        ordering = ["id"]

    def __str__(self):
        return f"{self.regla or self.tipo}: {self.mensaje[:60]}"
