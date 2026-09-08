"""Autenticación por API Key para clientes máquina (el ERP).

El ERP envía la credencial en la cabecera::

    Authorization: Api-Key <prefijo>.<secreto>

A diferencia del frontend (que usa JWT y se autentica como un ``Usuario``), el
ERP no es una persona: se autentica como un :class:`PrincipalLlaveApi`, que
expone la llave (y con ella su usuario) para que ``apps.seguridad.alcance`` le
dé exactamente el mismo alcance que a esa persona.
"""
from rest_framework import authentication, exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.seguridad.models import LlaveApi

PALABRA_CLAVE = "Api-Key"

# Nombres de las cookies de sesión. Viven aquí porque los usan tanto quien las
# lee (esta autenticación) como quien las escribe (las vistas de sesión).
COOKIE_ACCESO = "access_token"
COOKIE_REFRESCO = "refresh_token"
COOKIE_DISPOSITIVO = "mfa_dispositivo"


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
        # La persona en cuyo nombre actúa. `apps.seguridad.alcance` la usa para
        # darle exactamente el mismo alcance que a ella.
        self.usuario = llave.usuario

    def __str__(self):
        return f"ERP[{self.usuario}]"


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
            llave = LlaveApi.objects.select_related("usuario").get(prefijo=prefijo)
        except LlaveApi.DoesNotExist:
            raise exceptions.AuthenticationFailed("API Key inválida.")
        if not llave.verificar_secreto(secreto):
            raise exceptions.AuthenticationFailed("API Key inválida.")
        if not llave.esta_vigente():
            raise exceptions.AuthenticationFailed("API Key inactiva o expirada.")
        # Desactivar a la persona corta el acceso de todas sus llaves de golpe,
        # sin tener que revocarlas una a una.
        if not llave.usuario.is_active:
            raise exceptions.AuthenticationFailed("El usuario está inactivo.")
        llave.registrar_uso()
        return (PrincipalLlaveApi(llave), llave)

    def authenticate_header(self, request):
        # Provoca un 401 (en vez de 403) cuando falta o falla la credencial.
        return self.palabra_clave


class JwtDeCookie(JWTAuthentication):
    """JWT leído de la cookie ``httpOnly``, nunca de la cabecera.

    La sesión del navegador vive en una cookie que el JavaScript no puede leer:
    un XSS ya no se lleva la sesión, que es la razón de existir de todo esto.
    Aceptar además ``Authorization: Bearer`` echaría a perder la garantía, porque
    el front tendría que guardar el token en algún sitio legible para poder
    mandarlo.

    Los clientes que no son navegador no se quedan fuera: el ERP se autentica con
    ``Authorization: Api-Key``, que es otro camino y no pasa por aquí.

    Un token ilegible sube como error en vez de devolver ``None``: así el front
    distingue "expiró, refresca" de "no hay sesión", en lugar de recibir un 403
    genérico del control de permisos. Puede hacerse porque las rutas públicas
    —registro, verificación, reenvío— declaran ``authentication_classes = []``,
    así que una cookie caducada no impide registrarse.
    """

    def authenticate(self, request):
        crudo = request.COOKIES.get(COOKIE_ACCESO)
        if not crudo:
            return None
        validado = self.get_validated_token(crudo)
        return self.get_user(validado), validado
