"""Dígito de verificación del NIT colombiano.

El DV no es un dato que el usuario elija: se calcula a partir del número. La
DIAN lo comprueba en cada documento (reglas CAJ24, CAK24 y la del Prestador de
Servicios), así que guardarlo tal como lo teclearon —o tal como lo devolvió un
tercero— es rechazo seguro.
"""
from __future__ import annotations

# Pesos del algoritmo módulo 11 de la DIAN, aplicados de derecha a izquierda.
_PESOS = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]

# Código de la lista TipoDocumento de la DIAN para el NIT: es el único tipo de
# identificación que lleva dígito de verificación.
CODIGO_NIT = "31"


def digito_verificacion(numero: str) -> str:
    """Devuelve el DV del NIT, o ``""`` si el número no es utilizable.

    Se ignora todo lo que no sea dígito (puntos, guiones, un DV ya pegado con
    guion no: eso hay que quitarlo antes) y se aplica el módulo 11.
    """
    digitos = "".join(c for c in str(numero or "") if c.isdigit())
    if not digitos or len(digitos) > len(_PESOS):
        return ""
    total = sum(int(d) * _PESOS[i] for i, d in enumerate(reversed(digitos)))
    resto = total % 11
    return str(resto if resto < 2 else 11 - resto)


def dv_de_entidad(entidad) -> str:
    """El DV que le corresponde a un emisor/adquiriente, ``""`` si no es NIT.

    Solo los NIT llevan DV; una cédula con un DV inventado también la rechaza
    la DIAN, así que en los demás tipos se deja vacío.
    """
    tipo = getattr(entidad, "tipo_identificacion", None)
    if tipo is None or getattr(tipo, "codigo", "") != CODIGO_NIT:
        return ""
    return digito_verificacion(entidad.numero_identificacion)
