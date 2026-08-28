"""Opciones compartidas por lo que se emite ante la DIAN."""
from django.db import models


class Ambiente(models.IntegerChoices):
    """Ambiente DIAN contra el que se emite.

    Los dos valores los define la DIAN y son los mismos para factura, documento
    soporte y nómina: entran en el XML (``ProfileExecutionID`` / el atributo
    ``Ambiente``) y en el CUFE/CUNE. Viven aquí y no repetidos en cada modelo
    porque dos listas que puedan divergir no aportan nada y sí se pueden
    contradecir.
    """

    PRODUCCION = 1, "Producción"
    PRUEBAS = 2, "Habilitación"
