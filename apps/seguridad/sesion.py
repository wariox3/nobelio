"""Emisión y cierre de la sesión en cookies.

Un solo sitio escribe las cookies de sesión, y es el mismo por el que pasan los
dos caminos del ingreso —con segundo factor y sin él—. Tenerlo suelto en cada
vista es como se acaba emitiendo una sesión por una rama que se saltó un paso.
"""
from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.utils import timezone
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from apps.seguridad import mfa as servicio_mfa
from apps.seguridad.autenticacion import (
    COOKIE_ACCESO,
    COOKIE_DISPOSITIVO,
    COOKIE_REFRESCO,
)

# Instante en que arrancó la sesión. Sobrevive a las rotaciones, a diferencia de
# `iat`, que `set_iat()` reescribe en cada refresco. Es lo que permite el tope
# absoluto: sin él, rotar a diario haría que una sesión no caducara nunca.
CLAIM_INICIO = "ses"

_VIDA_ACCESO = int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())
_VIDA_REFRESCO = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
_VIDA_DISPOSITIVO = int(servicio_mfa.DURACION_DISPOSITIVO.total_seconds())


def _poner(respuesta, nombre, valor, segundos):
    respuesta.set_cookie(
        nombre,
        valor,
        max_age=segundos,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        domain=settings.AUTH_COOKIE_DOMAIN,
    )


def poner_cookies(respuesta, acceso, refresco=None):
    _poner(respuesta, COOKIE_ACCESO, acceso, _VIDA_ACCESO)
    if refresco:
        _poner(respuesta, COOKIE_REFRESCO, refresco, _VIDA_REFRESCO)


def emitir(usuario, datos, *, request=None, recordar_dispositivo=False):
    """Cierra el ingreso: marca la entrada, emite los JWT y arma la respuesta.

    Único punto donde se emiten cookies de sesión. Si la cuenta tiene segundo
    factor, solo se llega aquí después de resolverlo.
    """
    update_last_login(None, usuario)

    refresco = RefreshToken.for_user(usuario)
    refresco[CLAIM_INICIO] = int(timezone.now().timestamp())
    acceso = str(refresco.access_token)

    cuerpo = dict(datos)
    if settings.DEBUG:
        # Solo en desarrollo: sin esto no hay forma de probar con curl o Postman,
        # que no guardan cookies. En producción el token no sale del navegador.
        cuerpo["access_token"] = acceso

    respuesta = Response(cuerpo)
    poner_cookies(respuesta, acceso, str(refresco))

    if recordar_dispositivo:
        token = servicio_mfa.recordar_dispositivo(
            usuario,
            agente=(request.META.get("HTTP_USER_AGENT") if request else None),
            ip=(request.META.get("REMOTE_ADDR") if request else None),
        )
        _poner(respuesta, COOKIE_DISPOSITIVO, token, _VIDA_DISPOSITIVO)

    return respuesta


def limpiar_cookies(respuesta):
    """Borra las cookies de sesión, no la del dispositivo.

    La del dispositivo dice "este navegador es de confianza", no "esta sesión
    está abierta": sobrevive al cierre de sesión a propósito y se revoca desde el
    perfil.
    """
    for nombre in (COOKIE_ACCESO, COOKIE_REFRESCO):
        respuesta.delete_cookie(nombre, domain=settings.AUTH_COOKIE_DOMAIN)
    return respuesta
