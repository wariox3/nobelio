"""Manejo homogéneo de errores de la API.

Todas las respuestas de error (4xx/5xx manejadas por DRF) se normalizan a:

    {
        "detail": "<mensaje legible>",
        "errores": { "<campo>": ["<msg>", ...], ... }
    }

``errores`` queda vacío (``{}``) cuando el error no es por campo.
"""
import logging

from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler

from apps.utilidades.almacenamiento import motivo_error_almacenamiento

logger = logging.getLogger(__name__)

MENSAJE_GENERICO = "La solicitud no es válida."


class ErrorSolicitud(APIException):
    """Error de negocio que se devuelve como 400 con un mensaje en ``detail``."""

    status_code = 400
    default_detail = MENSAJE_GENERICO
    default_code = "solicitud_invalida"


def entero_de_query(params, nombre):
    """Lee un filtro numérico de la query string, o lanza un 400 con sentido.

    Devuelve ``None`` si el parámetro no viene, que es lo que hace que el filtro
    no se aplique. Si viene con basura —``?emisor=abc``, ``?emisor=1;2``—
    responde 400 en vez del 500 que salía antes, cuando el valor llegaba tal
    cual a un ``filter(emisor=...)`` sobre una clave numérica.

    Se eligió el 400 y no ignorar el valor: un filtro que no se aplica devuelve
    **más** filas de las pedidas, y quien integra lo descubre tarde y mal. El
    alcance ya acota el queryset, así que no hay fuga; pero sí una respuesta que
    miente sobre lo que se preguntó.
    """
    valor = params.get(nombre)
    if valor is None or valor == "":
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErrorSolicitud(
            f"El filtro '{nombre}' tiene que ser un número entero; "
            f"se recibió '{valor}'."
        )


class ErrorPasarela(APIException):
    """Error al comunicarse con un servicio externo (p. ej. la DIAN): 502."""

    status_code = 502
    default_detail = "Error al comunicarse con un servicio externo."
    default_code = "error_pasarela"


def _limpiar(valor):
    """Convierte ErrorDetail (y estructuras anidadas) en str/list/dict planos."""
    if isinstance(valor, dict):
        return {clave: _limpiar(v) for clave, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpiar(v) for v in valor]
    return str(valor)


def _normalizar(data):
    """Devuelve ``(detail, errores)`` a partir del cuerpo de error de DRF."""
    if isinstance(data, dict):
        errores = {clave: _limpiar(v) for clave, v in data.items() if clave != "detail"}
        if "detail" in data:
            detail = str(data["detail"])
        elif "non_field_errors" in errores:
            primero = errores["non_field_errors"]
            detail = primero[0] if primero else MENSAJE_GENERICO
        else:
            detail = MENSAJE_GENERICO
        return detail, errores
    if isinstance(data, (list, tuple)):
        items = _limpiar(data)
        return (items[0] if items else MENSAJE_GENERICO), {}
    return str(data), {}


def exception_handler(exc, context):
    """Exception handler de DRF que homogeniza el cuerpo de los errores.

    Además traduce los fallos del almacenamiento en la nube (botocore), que no
    son excepciones de DRF y sin esto acabarían en un 500 con traceback: una
    credencial B2 caducada es un problema de configuración conocido, no un bug,
    y merece un 502 con un mensaje que diga qué revisar.
    """
    motivo = motivo_error_almacenamiento(exc)
    if motivo is not None:
        # El traceback original (con el keyID) queda en el log del servidor,
        # nunca en la respuesta.
        logger.exception("Fallo del almacenamiento de archivos: %s", exc)
        exc = ErrorPasarela(motivo)

    respuesta = drf_exception_handler(exc, context)
    if respuesta is None:
        return None
    detail, errores = _normalizar(respuesta.data)
    respuesta.data = {"detail": detail, "errores": errores}
    return respuesta


def error_pasarela_dian(exc):
    """Convierte un fallo de red con la DIAN en un 502 con el fault dentro.

    La DIAN devuelve el motivo real en el ``soap:Fault`` del cuerpo, no en el
    código HTTP: sin extraerlo, todos los errores se leen igual ("502").
    """
    import requests

    from apps.dian import soap

    fault = ""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        fault = soap.extraer_fault(exc.response.content)
    return ErrorPasarela(f"Error al comunicarse con la DIAN: {fault or exc}")
