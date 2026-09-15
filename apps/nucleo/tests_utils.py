"""Ayudas para leer en las pruebas el cuerpo de error de la API.

El cuerpo es ``{"detail": ..., "errores": [{"codigo": ..., "mensaje": ...}]}``
y el campo no viaja aparte: va delante del mensaje (``"moneda: Este campo es
obligatorio."``). Para las pruebas es más cómodo tenerlo agrupado por campo,
que es como se hacían las comprobaciones antes de aplanar la lista.
"""
import re

# La ruta que antepone `apps.nucleo.api` a los errores de un campo:
# `emisor`, `adquiriente.pais`, `detalles[0].impuestos[1].tributo`.
_CON_RUTA = re.compile(r"^(?P<ruta>[A-Za-z_]\w*(?:\.\w+|\[\d+\])*): (?P<texto>.*)$", re.S)


def errores_por_campo(respuesta):
    """``{ruta: [mensajes sin la ruta]}``; la ruta es ``""`` si no hay campo."""
    agrupados = {}
    for error in respuesta.data["errores"]:
        coincidencia = _CON_RUTA.match(error["mensaje"])
        if coincidencia:
            ruta, texto = coincidencia["ruta"], coincidencia["texto"]
        else:
            ruta, texto = "", error["mensaje"]
        agrupados.setdefault(ruta, []).append(texto)
    return agrupados


def codigos(respuesta):
    """Los códigos de error de la respuesta, en su orden."""
    return [error["codigo"] for error in respuesta.data["errores"]]
