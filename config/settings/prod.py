"""Settings de producción."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import configurar_cache, env

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

# --- Caché compartida entre workers ---
# Obligatoria en producción: con la de por-proceso, los topes de peticiones se
# multiplican por el número de workers y no protegen de nada (ver base.py). Es
# el Redis administrado de la misma red (`REDIS_URL`), y sin él la app no
# arranca: mejor un error al desplegar que unos topes que no cuentan.
_url_redis = env("REDIS_URL", default="")
if not _url_redis.startswith(("redis://", "rediss://")):
    raise ImproperlyConfigured(
        "REDIS_URL es obligatoria en producción (redis://… o rediss://…): sin "
        "caché compartida, los topes de peticiones se multiplicarían por el "
        "número de workers."
    )
CACHES = {"default": configurar_cache(_url_redis)}
