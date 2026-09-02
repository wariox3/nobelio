"""Límite de peticiones por credencial.

El proyecto autentica de dos formas —API Key para el ERP y JWT para el
frontend—, y el `UserRateThrottle` de DRF solo entiende la segunda: construye
su clave de caché con `request.user.pk`, y el principal de una API Key
(`PrincipalLlaveApi`) no es un modelo, así que no tiene `pk` y la petición
revienta con un `AttributeError`.

Aquí se resuelve la identidad de las dos: la llave se cuenta por su id de fila,
que es lo que de verdad identifica a la integración —una cuenta puede tener
varias llaves y conviene poder estrangular una sin tocar las demás—, y el
usuario humano por su clave primaria.
"""
from rest_framework.throttling import UserRateThrottle


class LimitePorCredencial(UserRateThrottle):
    """Cuenta por API Key o por usuario, lo que traiga la petición."""

    scope = "user"

    def get_cache_key(self, request, view):
        usuario = getattr(request, "user", None)
        if usuario is None or not usuario.is_authenticated:
            # Sin credencial: de esta se ocupa `AnonRateThrottle`.
            return None

        llave = getattr(usuario, "llave", None)
        if llave is not None:
            identidad = f"llave-{llave.pk}"
        else:
            identidad = f"usuario-{usuario.pk}"
        return self.cache_format % {"scope": self.scope, "ident": identidad}
