"""Traducción de los fallos de la DIAN a errores de la API.

Este módulo es la frontera entre el pipeline y la capa HTTP, y es el único de
``apps.dian`` que sabe que existe una API REST: el resto —``servicios``,
``soap``, ``ubl``, ``firma``— no conoce ni peticiones ni respuestas, y conviene
que siga así (es lo que permite llamar al pipeline desde una cola sin
arrastrar nada de DRF).

Vivía en ``apps.nucleo.api``, que es la capa base sobre la que se apoya todo lo
demás: para extraer el ``soap:Fault`` tenía que importar ``apps.dian`` desde
dentro de la función, invirtiendo las capas. Aquí el import es normal, porque
``apps.dian`` ya depende de ``apps.nucleo`` y no al revés.
"""
import requests

from apps.dian import soap
from apps.nucleo.api import ErrorPasarela


def error_pasarela_dian(exc):
    """Convierte un fallo de red con la DIAN en un 502 con el fault dentro.

    La DIAN devuelve el motivo real en el ``soap:Fault`` del cuerpo, no en el
    código HTTP: sin extraerlo, todos los errores se leen igual ("502").
    """
    fault = ""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        fault = soap.extraer_fault(exc.response.content)
    return ErrorPasarela(f"Error al comunicarse con la DIAN: {fault or exc}")
