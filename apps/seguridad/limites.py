"""Límite de peticiones por credencial.

El proyecto autentica de dos formas —API Key para el ERP y JWT para el
frontend—, y el `UserRateThrottle` de DRF solo entiende la segunda: construye
su clave de caché con `request.user.pk`, y el principal de una API Key
(`PrincipalLlaveApi`) no es un modelo, así que no tiene `pk` y la petición
revienta con un `AttributeError`.

Aquí se resuelve la identidad de las dos: la llave se cuenta por su id de fila,
que es lo que de verdad identifica a la integración —una persona puede tener
varias llaves y conviene poder estrangular una sin tocar las demás—, y el
usuario humano por su clave primaria.
"""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


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


class _LimitePorAtributo(SimpleRateThrottle):
    """Base de los topes cuyo `scope` lo declara la vista en un atributo.

    Es el mecanismo de ``ScopedRateThrottle``: como el rate depende de la vista,
    que en el constructor todavía no se conoce, se resuelve en `allow_request`.
    Donde la vista no declara el atributo, el tope no se aplica.
    """

    scope_attr = None

    def __init__(self):
        # A propósito sin `super().__init__()`.
        pass

    def allow_request(self, request, view):
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class LimiteRafaga(_LimitePorAtributo):
    """Tope corto por IP, además del sostenido.

    Un solo tope obliga a elegir entre estorbar a la gente y dejar pasar los
    ataques: `10/hora` frena poco un guion que dispara diez veces en dos
    segundos, y `10/minuto` no frena nada repartido en una hora. Con los dos, el
    corto corta la ráfaga y el largo el goteo, y ninguno de los dos tiene que
    ser incómodo para una persona.
    """

    scope_attr = "throttle_scope_rafaga"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LimitePorCorreo(_LimitePorAtributo):
    """Cuenta por la dirección de correo del cuerpo, no por IP.

    Los topes por IP no protegen a la víctima: quien rote direcciones puede
    pedir cien veces el reenvío de verificación del correo de otro y llenarle la
    bandeja, o repartir un ataque de contraseñas contra una misma cuenta entre
    muchas IP. Contar por el correo cierra las dos cosas, porque lo que se
    protege es la cuenta, no el origen.

    Convive con los topes por IP en vez de sustituirlos: son ataques distintos
    —uno castiga al origen ruidoso, el otro protege al destinatario— y DRF
    aplica todos los throttles, así que basta el más estricto para frenar.

    La vista dice qué tope usar con ``throttle_scope_correo``; donde no lo
    declara, esta clase no hace nada. Es el mismo mecanismo de
    ``ScopedRateThrottle``, de ahí que el `rate` se resuelva en `allow_request`
    y no en el constructor.
    """

    scope_attr = "throttle_scope_correo"

    def get_cache_key(self, request, view):
        datos = request.data if isinstance(request.data, dict) else {}
        correo = (datos.get("email") or "").strip().lower()
        if not correo:
            # Sin correo en el cuerpo no hay a quién proteger; del origen se
            # ocupan los topes por IP.
            return None
        return self.cache_format % {"scope": self.scope, "ident": correo}
