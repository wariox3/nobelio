"""Días hábiles en Colombia: de lunes a viernes, sin los festivos del catálogo.

Los plazos de los eventos RADIAN se cuentan en días hábiles (el 033 va dentro
de los 3 siguientes al 032). Los festivos salen de ``Festivo``, que se mantiene
a mano cada año: si falta el año que hace falta, no se adivina, se lanza
``FestivosNoCargados``, porque un plazo mal contado es un evento que la DIAN
rechaza.
"""
from datetime import date, timedelta

from apps.catalogos.models import Festivo


class FestivosNoCargados(Exception):
    """No están los festivos de un año que hace falta para contar."""


def es_habil(fecha: date) -> bool:
    """¿``fecha`` es día hábil?"""
    return fecha.weekday() < 5 and fecha not in _festivos(fecha.year)


def sumar_dias_habiles(desde: date, dias: int) -> date:
    """El día hábil número ``dias`` después de ``desde``, sin contar ``desde``.

    ``sumar_dias_habiles(viernes, 1)`` es el lunes siguiente (o el martes, si
    el lunes es festivo). Solo exige los festivos de los años que recorre.
    """
    if dias < 1:
        raise ValueError("dias tiene que ser 1 o más.")
    por_anio = {}
    fecha, contados = desde, 0
    while contados < dias:
        fecha += timedelta(days=1)
        if fecha.year not in por_anio:
            por_anio[fecha.year] = _festivos(fecha.year)
        if fecha.weekday() < 5 and fecha not in por_anio[fecha.year]:
            contados += 1
    return fecha


def _festivos(anio):
    """Los festivos de ``anio``; si no hay ninguno, el año no está cargado."""
    festivos = set(
        Festivo.objects.filter(fecha__year=anio, activo=True).values_list("fecha", flat=True)
    )
    if not festivos:
        raise FestivosNoCargados(
            f"Faltan los festivos de {anio} en el catálogo "
            "(datos/listas/calendario/Festivo.gc)."
        )
    return festivos
