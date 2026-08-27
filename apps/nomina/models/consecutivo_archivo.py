"""Consecutivo de archivos enviados a la DIAN, por emisor y año."""
from django.db import models, transaction

from apps.nucleo.models import ModeloConFechas


class ConsecutivoArchivo(ModeloConFechas):
    """Numerador de los archivos de nómina que se le mandan a la DIAN.

    El nombre del XML y el del ZIP llevan un consecutivo **de archivos
    enviados** —ocho dígitos hexadecimales— que no tiene nada que ver con el
    consecutivo del documento y que, dice el anexo, se reinicia el 1 de enero.
    Por eso se cuenta aparte, por emisor y año, y no sale de la nómina.
    """

    anio = models.PositiveSmallIntegerField("año")
    valor = models.PositiveIntegerField("último consecutivo usado", default=0)

    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.CASCADE,
        related_name="consecutivos_archivo_nomina", verbose_name="emisor",
    )

    class Meta:
        db_table = "nom_consecutivo_archivo"
        verbose_name = "consecutivo de archivos"
        verbose_name_plural = "consecutivos de archivos"
        ordering = ["-anio"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "anio"], name="consecutivo_archivo_unico_por_anio",
            )
        ]

    @classmethod
    def siguiente(cls, emisor, anio: int) -> int:
        """Reserva y devuelve el siguiente consecutivo del emisor para ese año.

        Bloquea la fila mientras la incrementa: dos envíos simultáneos del mismo
        emisor no pueden llevarse el mismo número, que la DIAN vería como un
        archivo repetido.
        """
        with transaction.atomic():
            fila, _ = cls.objects.select_for_update().get_or_create(
                emisor=emisor, anio=anio,
            )
            fila.valor += 1
            fila.save(update_fields=["valor", "actualizado_en"])
            return fila.valor

    def __str__(self):
        return f"{self.emisor} {self.anio}: {self.valor}"
