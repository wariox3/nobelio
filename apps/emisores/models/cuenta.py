"""Cuenta: agrupador de emisores."""
from django.db import models


class Cuenta(models.Model):
    """Agrupa emisores bajo un nombre, p. ej. el ERP que los integra.

    Solo agrupa: no da ni quita acceso a nada (eso es cosa del alcance, ver
    ``apps.seguridad.alcance``). Un emisor puede no tener cuenta.
    """

    nombre = models.CharField("nombre", max_length=150, unique=True)

    class Meta:
        db_table = "emi_cuenta"
        verbose_name = "cuenta"
        verbose_name_plural = "cuentas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
