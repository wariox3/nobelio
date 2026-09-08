"""Settings de producción."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# Seguridad endurecida para producción.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --- Caché compartida entre workers ---------------------------------------
# Obligatoria en producción: con la de por-proceso, los topes de peticiones se
# multiplican por el número de workers y no protegen de nada (ver base.py). Por
# defecto va contra PostgreSQL, que ya está, en vez de pedir un servicio nuevo;
# la tabla la crea la migración `nucleo.0002_tabla_de_cache`.
CACHES = {"default": env.cache("CACHE_URL", default="dbcache://cache_general")}
