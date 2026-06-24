"""Autenticación por API Key para clientes máquina (el ERP).

El ERP envía la credencial en la cabecera::

    Authorization: Api-Key <prefijo>.<secreto>

A diferencia del frontend (que usa JWT y se autentica como un ``Usuario``), el
ERP no es una persona: se autentica como un :class:`PrincipalLlaveApi`, que
expone el emisor al que pertenece la llave para que los permisos y las vistas
puedan delimitar sobre qué emisor opera.
"""
from rest_framework import authentication, exceptions

from apps.seguridad.models import LlaveApi

PALABRA_CLAVE = "Api-Key"


class PrincipalLlaveApi:
    """Identidad de un cliente máquina autenticado por API Key.

    Imita lo justo de la interfaz de usuario de Django para que las clases de
    permiso estándar (p. ej. ``IsAuthenticated``) lo den por autenticado.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, llave):
        self.llave = llave
        self.emisor = llave.emisor

    def __str__(self):
        return f"ERP[{self.emisor}]"


class LlaveApiAuthentication(authentication.BaseAuthentication):
    """Autentica peticiones que traen ``Authorization: Api-Key <prefijo>.<secreto>``."""

    palabra_clave = PALABRA_CLAVE

    def authenticate(self, request):
        cabecera = authentication.get_authorization_header(request).split()
        if not cabecera or cabecera[0].lower() != self.palabra_clave.lower().encode():
            # No es una credencial Api-Key: deja que lo intente otra clase (JWT).
            return None
        if len(cabecera) == 1:
            raise exceptions.AuthenticationFailed(
                "Cabecera Api-Key inválida: falta la credencial."
            )
        if len(cabecera) > 2:
            raise exceptions.AuthenticationFailed(
                "Cabecera Api-Key inválida: la credencial no puede llevar espacios."
            )
        try:
            credencial = cabecera[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed(
                "Cabecera Api-Key inválida: codificación incorrecta."
            )
        return self._autenticar(credencial)

    def _autenticar(self, credencial):
        prefijo, separador, secreto = credencial.partition(".")
        if not separador or not prefijo or not secreto:
            raise exceptions.AuthenticationFailed("API Key inválida.")
        try:
            llave = LlaveApi.objects.select_related("emisor").get(prefijo=prefijo)
        except LlaveApi.DoesNotExist:
            raise exceptions.AuthenticationFailed("API Key inválida.")
        if not llave.verificar_secreto(secreto):
            raise exceptions.AuthenticationFailed("API Key inválida.")
        if not llave.esta_vigente():
            raise exceptions.AuthenticationFailed("API Key inactiva o expirada.")
        llave.registrar_uso()
        return (PrincipalLlaveApi(llave), llave)

    def authenticate_header(self, request):
        # Provoca un 401 (en vez de 403) cuando falta o falla la credencial.
        return self.palabra_clave
