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
# la tabla la crea la migración `nucleo.0001_tabla_de_cache`.
#
# Cuesta varios viajes a la base por cada tope que cuenta (contar, savepoint,
# buscar, escribir). Si con la base fuera del servidor llega a pesar, la salida
# es un Redis local con `CACHE_URL=redis://127.0.0.1:6379/0` —hace falta el
# paquete `redis`—; se dejó para más adelante para no sumar un servicio.
# Vacía cuenta como no definida, igual que en base.py.
CACHES = {
    "default": env.cache_url_config(
        env("CACHE_URL", default="") or "dbcache://cache_general"
    )
}

# Con el `MAX_ENTRIES` por defecto (300), al pasarse la tabla borra un tercio de
# las claves que siguen vivas, y aquí cada clave es el contador de un tope: se
# reiniciaban a medio llenar, y quien rotara IPs o correos podía provocarlo a
# propósito para volver a empezar. Más alto no sale gratis: la tabla solo se
# limpia de vencidas al pasar del límite, y cada escritura hace un `COUNT(*)`
# de toda la tabla (medido en local: ~0,5 ms con 5 000 filas, ~1,5 ms con
# 50 000). 5 000 contadores vivos a la vez es muy por encima del tráfico real.
# Solo aplica a la de PostgreSQL; un `?max_entries=` en la URL manda.
if CACHES["default"]["BACKEND"].endswith(".DatabaseCache"):
    CACHES["default"].setdefault("OPTIONS", {}).setdefault("MAX_ENTRIES", 5000)
