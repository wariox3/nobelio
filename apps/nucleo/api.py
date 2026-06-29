"""Manejo homogéneo de errores de la API.

Todas las respuestas de error (4xx/5xx manejadas por DRF) se normalizan a:

    {
        "detail": "<mensaje legible>",
        "errores": { "<campo>": ["<msg>", ...], ... }
    }

``errores`` queda vacío (``{}``) cuando el error no es por campo.
"""
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler

MENSAJE_GENERICO = "La solicitud no es válida."


class ErrorSolicitud(APIException):
    """Error de negocio que se devuelve como 400 con un mensaje en ``detail``."""

    status_code = 400
    default_detail = MENSAJE_GENERICO
    default_code = "solicitud_invalida"


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
    """Exception handler de DRF que homogeniza el cuerpo de los errores."""
    respuesta = drf_exception_handler(exc, context)
    if respuesta is None:
        return None
    detail, errores = _normalizar(respuesta.data)
    respuesta.data = {"detail": detail, "errores": errores}
    return respuesta
