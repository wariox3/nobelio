"""Consecutivo de archivos de documento equivalente enviados a la DIAN."""
from django.db import models, transaction

from apps.nucleo.models import ModeloConFechas


class ConsecutivoArchivoDocumentoEquivalente(ModeloConFechas):
    """Numerador de los archivos de documento equivalente, por emisor y año.

    El nombre del XML y del ZIP lleva un consecutivo **de archivos enviados**
    —ocho dígitos hexadecimales— que no tiene nada que ver con el consecutivo
    del documento y que, dice el numeral 8.13.5, se reinicia el 1 de enero.

    Es un contador aparte del de nómina (``nomina.ConsecutivoArchivo``) y no el
    mismo con un tipo: son dos familias de documentos con sus propios anexos y
    sus propios prefijos, y compartir el contador ataría el ritmo de una al de
    la otra sin que ninguna regla lo pida. El ``ds``, el ``ncs`` y el ``ars``
    de esta familia sí lo comparten.
    """

    anio = models.PositiveSmallIntegerField("año")
    valor = models.PositiveIntegerField("último consecutivo usado", default=0)

    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.CASCADE,
        related_name="consecutivos_archivo_documento_equivalente",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "doc_consecutivo_archivo_de"
        verbose_name = "consecutivo de archivos de documento equivalente"
        verbose_name_plural = "consecutivos de archivos de documento equivalente"
        ordering = ["-anio"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "anio"],
                name="consecutivo_archivo_de_unico_por_anio",
            )
        ]

    @classmethod
    def siguiente(cls, emisor, anio: int) -> int:
        """Reserva y devuelve el siguiente consecutivo del emisor para ese año.

        Bloquea la fila mientras la incrementa: en un punto de venta los envíos
        concurrentes son la norma, no la excepción, y dos que se llevaran el
        mismo número le llegarían a la DIAN como un archivo repetido.
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
