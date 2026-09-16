"""Manejo homogéneo de errores de la API.

Todas las respuestas de error (4xx/5xx manejadas por DRF) se normalizan a:

    {
        "detail": "<mensaje legible>",
        "errores": [{"codigo": "<código>", "mensaje": "<mensaje>"}, ...]
    }

La forma es fija a propósito, por decisión de MarioA: antes ``errores`` era un
objeto que repetía la forma de la petición (``{"detalles": [{"impuestos":
...}]}``), y un cliente generado no puede tipar un objeto cuyas claves cambian
con cada error. Ahora cada error tiene siempre las mismas dos claves.

- ``codigo`` es lo que el cliente mapea. Los de DRF van tal cual (``required``,
  ``invalid``, ``does_not_exist``, ``not_found``, ``throttled``…); los propios
  del proyecto, en español (``solicitud_invalida``, ``campo_desconocido``…).
- ``mensaje`` es el texto para la persona. El campo no viaja aparte: cuando el
  error es de un campo, su ruta va delante
  (``detalles[0].impuestos[1].tributo: Este campo es obligatorio.``); cuando no,
  el mensaje va solo.

La lista nunca va vacía: un 404 o un error de negocio llevan un elemento con su
código y el mismo texto que ``detail``. Así el cliente decide siempre por
``codigo``, venga el fallo de donde venga.
"""
import logging
import uuid

from rest_framework.exceptions import APIException
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler

from apps.utilidades.almacenamiento import motivo_error_almacenamiento

logger = logging.getLogger(__name__)

MENSAJE_GENERICO = "La solicitud no es válida."
# El de `ValidationError` cuando nadie le dio otro.
CODIGO_POR_DEFECTO = "invalid"


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


def uuid_de_query(params, nombre):
    """Lee un filtro UUID de la query string, o lanza un 400 con sentido.

    Igual que ``entero_de_query``, pero para las claves UUID: un valor que no es
    UUID llegaría a un ``filter(pk=...)`` y saldría como 500.
    """
    valor = params.get(nombre)
    if valor is None or valor == "":
        return None
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise ErrorSolicitud(
            f"El filtro '{nombre}' tiene que ser un UUID; se recibió '{valor}'."
        )


class ErrorPasarela(APIException):
    """Error al comunicarse con un servicio externo (p. ej. la DIAN): 502."""

    status_code = 502
    default_detail = "Error al comunicarse con un servicio externo."
    default_code = "error_pasarela"


def cuerpo_de_error(mensaje, codigo):
    """El cuerpo de error para las vistas que responden sin lanzar excepción.

    Algunas rutas de sesión arman su propio ``Response`` para decidir el status
    o las cookies; con esto no se salen de la forma común.
    """
    return {"detail": mensaje, "errores": [{"codigo": codigo, "mensaje": mensaje}]}


def _unir(ruta, clave):
    """Suma una clave a la ruta: ``.campo`` o, si es un índice, ``[i]``."""
    if isinstance(clave, int):
        return f"{ruta}[{clave}]"
    return f"{ruta}.{clave}" if ruta else str(clave)


def _aplanar(valor, ruta=""):
    """Recorre el árbol de errores de DRF y da ``(ruta, mensaje)`` por hoja.

    Los objetos suman ``.clave`` a la ruta y las listas de objetos ``[i]``; una
    lista de mensajes sueltos son varios errores del mismo campo. ``detail`` y
    ``non_field_errors`` no son campos, así que no suman nada. Los mensajes son
    los ``ErrorDetail`` de DRF, que traen su código en ``.code``.
    """
    if isinstance(valor, dict):
        for clave, hijo in valor.items():
            if clave in ("detail", api_settings.NON_FIELD_ERRORS_KEY):
                yield from _aplanar(hijo, ruta)
            else:
                yield from _aplanar(hijo, _unir(ruta, clave))
    elif isinstance(valor, (list, tuple)):
        for indice, hijo in enumerate(valor):
            if isinstance(hijo, (dict, list, tuple)):
                yield from _aplanar(hijo, f"{ruta}[{indice}]")
            else:
                yield from _aplanar(hijo, ruta)
    else:
        yield ruta, valor


def _error(ruta, mensaje):
    texto = str(mensaje)
    return {
        "codigo": getattr(mensaje, "code", None) or CODIGO_POR_DEFECTO,
        "mensaje": f"{ruta}: {texto}" if ruta else texto,
    }


def _normalizar(data, codigo_de_respaldo):
    """Devuelve ``(detail, errores)`` a partir del cuerpo de error de DRF.

    ``detail`` es el mensaje propio del error si lo hay —el de una excepción de
    negocio, el primero sin campo— y el genérico cuando todo son fallos de
    campos, que ya se explican en la lista.
    """
    errores = [_error(ruta, mensaje) for ruta, mensaje in _aplanar(data)]

    if isinstance(data, dict):
        propio = data.get("detail") or data.get(api_settings.NON_FIELD_ERRORS_KEY)
    else:
        propio = data
    primero = next((m for _, m in _aplanar(propio)), None) if propio else None
    detail = str(primero) if primero is not None else MENSAJE_GENERICO

    if not errores:
        errores = [{"codigo": codigo_de_respaldo, "mensaje": detail}]
    return detail, errores


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
    detail, errores = _normalizar(
        respuesta.data, getattr(exc, "default_code", CODIGO_POR_DEFECTO),
    )
    respuesta.data = {"detail": detail, "errores": errores}
    return respuesta
