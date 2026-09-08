"""
Configuración base de Nobelio — Servicio de Facturación Electrónica DIAN Colombia.

Los settings se dividen en:
  - base.py : configuración común a todos los ambientes
  - dev.py  : desarrollo local
  - prod.py : producción

Selecciona el módulo con la variable de entorno DJANGO_SETTINGS_MODULE,
por defecto config.settings.dev (ver manage.py / wsgi.py / asgi.py).
"""
from datetime import timedelta
from pathlib import Path

import environ

from config import observabilidad

# BASE_DIR apunta a la raíz del repositorio (donde está manage.py).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Variables de entorno (.env) -------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# Clave Fernet con la que se cifra la clave del .p12 en la base
# (`Certificado.clave`; ver apps/utilidades/cifrado.py). Se genera con:
#
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# Va aparte de DJANGO_SECRET_KEY a propósito: la SECRET_KEY se rota cuando se
# quiere invalidar los JWT, y hacerlo no puede dejar ilegibles las claves de los
# certificados de todos los emisores.
#
# No tiene `default`: si falta, `env()` lanza ImproperlyConfigured y el proyecto
# no arranca. Es deliberado. Un default silencioso significaría que un
# despliegue con la variable mal escrita seguiría funcionando y guardando las
# claves en claro, que es justamente lo que esto viene a evitar.
CERT_ENCRYPTION_KEY = env("CERT_ENCRYPTION_KEY")

# --- Aplicaciones -----------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    # Guarda los refresh anulados. Hace falta para que la rotación signifique
    # algo: sin la lista negra, el token viejo seguiría sirviendo después de
    # rotarlo, y cerrar sesión no cerraría nada.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
]

LOCAL_APPS = [
    "apps.nucleo",
    "apps.seguridad",
    "apps.catalogos",
    "apps.emisores",
    "apps.documentos",
    "apps.nomina",
    "apps.dian",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# API stateless: la autenticación la resuelve DRF por petición (JWT / API Key),
# así que no hacen falta los middleware de sesión ni de autenticación de Django.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Base de datos (PostgreSQL) ---------------------------------------------
# DATABASE_URL es obligatorio: si falta, la app falla al arrancar.
DATABASES = {
    "default": env.db_url("DATABASE_URL"),
}

# --- Modelo de usuario personalizado ---------------------------------------
AUTH_USER_MODEL = "seguridad.Usuario"

# --- Validación de contraseñas ---------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        # Por encima de los 8 de Django: el alta es pública y la contraseña es
        # lo único entre un desconocido y los documentos fiscales de su cuenta.
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internacionalización (Colombia) ---------------------------------------
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# --- Archivos estáticos y media --------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Pasarela de correo (Zinc) ---------------------------------------------
# Servicio HTTP propio para enviar correos; ver apps/utilidades/zinc.py.
ZINC_URL_BASE = env("ZINC_URL_BASE", default="http://zinc.semantica.com.co")
# Nombre que ve el destinatario como remitente del correo.
ZINC_NOMBRE_REMITENTE = env("ZINC_NOMBRE_REMITENTE", default="RedDoc ERP")

# --- Caché ---
# La usa solo el throttling, y por eso importa más de lo que parece: los
# contadores de LocMemCache viven en la memoria de cada worker, así que con N
# workers de Gunicorn un tope de 5/hora se convierte en 5·N/hora, y se reinicia
# en cada despliegue. En producción tiene que ser un backend compartido.
# `CACHE_URL` acepta también redis:// el día que haga falta.
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

# --- Registro público -------------------------------------------------------
# Página del sitio a la que apunta el correo de verificación; recibe el token
# por query string y lo reenvía a `POST /api/seguridad/registro/verificar/`.
# Vive fuera de la API porque la confirma una persona en el navegador, no un
# cliente de la API.
URL_VERIFICACION_CORREO = env(
    "URL_VERIFICACION_CORREO",
    default="http://localhost:4321/verificar-correo",
)

# --- Almacenamiento en Backblaze B2 (S3-compatible) ------------------------
# Credenciales de una "Application Key" de B2 con acceso al bucket. Si no están
# configuradas (dev/test), los archivos caen al almacenamiento local.
B2_BUCKET = env("B2_BUCKET", default="")
B2_ENDPOINT_URL = env("B2_ENDPOINT_URL", default="")  # p.ej. https://s3.us-west-004.backblazeb2.com
B2_REGION = env("B2_REGION", default="")              # p.ej. us-west-004
B2_KEY_ID = env("B2_KEY_ID", default="")              # keyID (access key)
B2_APP_KEY = env("B2_APP_KEY", default="")            # applicationKey (secret key)
B2_HABILITADO = bool(B2_BUCKET and B2_ENDPOINT_URL and B2_KEY_ID and B2_APP_KEY)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Captura de diagnóstico del tráfico SOAP con la DIAN (ver apps/dian/soap.py).
# Vacío —lo normal— no captura nada. Con un directorio, cada invocación deja ahí
# el sobre enviado y la respuesta cruda: lleva el documento firmado y el
# certificado, así que se activa para depurar un rechazo y se apaga después.
DIAN_DIRECTORIO_CAPTURA = env("DIAN_DIRECTORIO_CAPTURA", default="")

# --- Registro (logging) -----------------------------------------------------
# Todo sale por stdout y lo recoge journald a través de la unidad de systemd
# (ver docs/despliegue.md): la aplicación no abre ficheros, no rota nada y no
# hay que darle permisos sobre /var/log. Se lee con:
#
#     journalctl -u nobelio -f
#     journalctl -u nobelio --since today | grep estado=RECHAZADO
#
# El logger que importa es `apps`: de él cuelgan las trazas de emisión que deja
# `apps.dian.servicios` (firmado, enviado, respuesta de la DIAN, notificado).
# Se configura el padre y no cada módulo para poder subir o bajar el nivel de
# todo el proyecto en un sitio.
LOGGING = {
    "version": 1,
    # Los loggers de terceros que ya existan siguen funcionando; esta
    # configuración añade, no sustituye.
    "disable_existing_loggers": False,
    "formatters": {
        "nobelio": {
            # Fecha, nivel, módulo y la línea de `clave=valor` que arma
            # `apps.nucleo.registro.campos`. Pensado para grep, no para leerlo
            # con una herramienta.
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "nobelio",
        },
    },
    "loggers": {
        "apps": {
            "handlers": ["consola"],
            "level": env("LOG_LEVEL", default="INFO"),
            # Sin propagar: si no, la misma línea saldría dos veces en cuanto
            # el root tenga handler.
            "propagate": False,
        },
        # Los 500 del borde HTTP. Django los registra aquí, y hasta ahora se
        # perdían: es la otra mitad de poder investigar un fallo después.
        "django.request": {
            "handlers": ["consola"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# --- Sentry -----------------------------------------------------------------
# Solo se activa si hay DSN, así que en dev y en la suite no existe. El detalle
# de qué se envía y del filtro de nombres sensibles está en config/observabilidad.py,
# que es donde hay que apuntar cada campo nuevo que lleve un secreto.
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENTORNO = env("SENTRY_ENTORNO", default="desarrollo")
SENTRY_TRACES = env.float("SENTRY_TRACES", default=0.0)
SENTRY_RELEASE = env("SENTRY_RELEASE", default="")

observabilidad.configurar(
    dsn=SENTRY_DSN,
    entorno=SENTRY_ENTORNO,
    traces=SENTRY_TRACES,
    release=SENTRY_RELEASE,
)

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
    # Errores con cuerpo homogéneo: {"detail": ..., "errores": {...}}.
    "EXCEPTION_HANDLER": "apps.nucleo.api.exception_handler",
    # Dos vías coexistiendo: API Key (ERP) y JWT (frontend SPA). Ver
    # docs/autenticacion.md. Ambas son stateless (sin sesión, sin CSRF).
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.seguridad.autenticacion.LlaveApiAuthentication",
        # La sesión del navegador, solo desde la cookie httpOnly. Ver
        # apps/seguridad/autenticacion.py.
        "apps.seguridad.autenticacion.JwtDeCookie",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    # Un tope por credencial. No había ninguno: una API Key con un bucle roto
    # —o alguien probando ids— podía martillear la API sin encontrar resistencia,
    # y cada petición autenticada por llave cuesta un PBKDF2 y una escritura
    # (§D2), así que el coste de un abuso no es solo el tráfico.
    #
    # `user` cubre lo autenticado (llave o JWT) y `anon` el borde sin
    # credencial. Son ajustables por entorno para poder subirlos sin desplegar
    # código el día que un punto de venta con muchas cajas se quede corto.
    "DEFAULT_THROTTLE_CLASSES": [
        # El de DRF no vale: construye su clave con `request.user.pk` y el
        # principal de una API Key no es un modelo. Ver apps/seguridad/limites.py.
        "apps.seguridad.limites.LimitePorCredencial",
        "rest_framework.throttling.AnonRateThrottle",
        # Solo actúa donde la vista declara `throttle_scope`; en el resto no
        # estorba. Lo usan las rutas del registro, que son anónimas y caras:
        # cada una crea filas o dispara un correo por la pasarela.
        "rest_framework.throttling.ScopedRateThrottle",
        # Ráfaga corta por IP y tope por destinatario. Ver limites.py: DRF
        # aplica todos, así que frena el más estricto de los que apliquen.
        "apps.seguridad.limites.LimiteRafaga",
        "apps.seguridad.limites.LimitePorCorreo",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("THROTTLE_USUARIO", default="300/hour"),
        "anon": env("THROTTLE_ANONIMO", default="30/hour"),
        # --- Rutas públicas (sin credencial). Cada una lleva un tope
        # sostenido por IP y otro de ráfaga; las que reciben un correo llevan
        # además uno por destinatario, que es el que protege a la víctima
        # cuando el atacante rota de IP.
        #
        # Darse de alta tres veces en una hora desde la misma IP ya es raro, y
        # cada alta manda un correo.
        "registro": env("THROTTLE_REGISTRO", default="3/hour"),
        "registro_rafaga": env("THROTTLE_REGISTRO_RAFAGA", default="1/min"),
        # Confirmar se reintenta algo más (el cliente de correo precarga el
        # enlace, la persona recarga), pero es una operación de una vez.
        "verificacion": env("THROTTLE_VERIFICACION", default="10/hour"),
        "verificacion_rafaga": env("THROTTLE_VERIFICACION_RAFAGA", default="5/min"),
        # Reenviar manda un correo a una dirección que elige quien pide: sin
        # tope por destinatario es una máquina de spam contra terceros.
        "reenvio": env("THROTTLE_REENVIO", default="5/hour"),
        "reenvio_rafaga": env("THROTTLE_REENVIO_RAFAGA", default="2/min"),
        "reenvio_correo": env("THROTTLE_REENVIO_CORREO", default="3/hour"),
        # El login es el endpoint más atacado de cualquier servicio abierto. El
        # tope por correo es el que importa: sin él, repartir el ataque entre
        # muchas IP deja la cuenta sin protección ninguna.
        "login": env("THROTTLE_LOGIN", default="20/hour"),
        "login_rafaga": env("THROTTLE_LOGIN_RAFAGA", default="5/min"),
        "login_correo": env("THROTTLE_LOGIN_CORREO", default="10/hour"),
        # Segundo paso. El freno real a la fuerza bruta sobre seis dígitos no es
        # este, sino el contador de intentos del desafío, que vive en la base;
        # esto solo modera el tráfico.
        "mfa": env("THROTTLE_MFA", default="20/hour"),
        "mfa_rafaga": env("THROTTLE_MFA_RAFAGA", default="10/min"),
        # Reenviar manda un correo: más estrecho.
        "mfa_envio": env("THROTTLE_MFA_ENVIO", default="5/hour"),
        "mfa_envio_rafaga": env("THROTTLE_MFA_ENVIO_RAFAGA", default="2/min"),
        # Enrolar y desactivar, sobre la propia cuenta y ya autenticado.
        "mfa_gestion": env("THROTTLE_MFA_GESTION", default="20/hour"),
        "refresco": env("THROTTLE_REFRESCO", default="120/hour"),
    },
    # Cuántos proxies hay delante. Sin esto DRF usa la cabecera
    # `X-Forwarded-For` tal cual cuando viene, y como la manda el cliente,
    # cualquiera se salta todos los topes por IP mandando una distinta en cada
    # petición. Con el número exacto se lee la posición correcta de la cadena,
    # que es la única que el proxy no deja falsificar.
    #   0 = sin proxy (usa REMOTE_ADDR)   1 = un proxy delante
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
}

# --- JWT (frontend SPA) -----------------------------------------------------
SIMPLE_JWT = {
    # Corto a propósito: con la sesión en cookie y rotación de refresh, el access
    # es lo único que viaja en cada petición y no se puede revocar antes de que
    # venza. 15 minutos es el precio de no consultar la lista negra siempre.
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_MINUTOS", default=15)
    ),
    # Vencimiento por *inactividad*: la rotación lo corre hacia adelante en cada
    # refresco. El tope absoluto lo pone SESION_MAXIMA.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DIAS", default=1)),
    # Cada refresco entrega un refresh nuevo y anula el anterior: un token robado
    # deja de servir en cuanto el dueño legítimo refresca.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# Tope absoluto de una sesión, aunque se use sin interrupciones. Sin él, la
# rotación corre el vencimiento indefinidamente y una sesión no caduca nunca:
# un refresh robado y rotado a diario viviría para siempre, y el segundo factor
# solo se verifica al iniciar sesión. Viaja en el claim propio `ses`.
SESION_MAXIMA = timedelta(days=env.int("SESION_MAXIMA_DIAS", default=30))

# --- Sesión en cookie -------------------------------------------------------
# El navegador recibe los JWT en cookies httpOnly, que el JavaScript no puede
# leer: un XSS ya no se lleva la sesión. Los clientes que no son navegador
# (ERP, curl) siguen usando `Authorization`, sea API Key o Bearer.
AUTH_COOKIE_DOMAIN = env("AUTH_COOKIE_DOMAIN", default=None) or None
# Obligatorio en producción; en desarrollo estorba porque no hay HTTPS.
AUTH_COOKIE_SECURE = env.bool("AUTH_COOKIE_SECURE", default=not DEBUG)
# `Lax` es lo que frena el CSRF: el navegador no manda la cookie en peticiones
# de escritura que vengan de otro sitio. Exige que la SPA y la API compartan
# dominio registrable (app.midominio.com y api.midominio.com).
AUTH_COOKIE_SAMESITE = env("AUTH_COOKIE_SAMESITE", default="Lax")

# --- Segundo factor ---------------------------------------------------------
# Clave Fernet que cifra los secretos TOTP y con la que se hashean los códigos.
# Separada de CERT_ENCRYPTION_KEY y de SECRET_KEY a propósito: son secretos de
# dominios distintos y rotar uno no puede dejar inservibles los otros.
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MFA_ENCRYPTION_KEY = env("MFA_ENCRYPTION_KEY", default="")

# --- CORS (la SPA vive en otro dominio) -------------------------------------
# Orígenes permitidos del frontend, p. ej. https://app.midominio.com
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
# Activar solo si el refresh viaja en cookie httpOnly.
CORS_ALLOW_CREDENTIALS = env.bool("CORS_ALLOW_CREDENTIALS", default=False)

# ===========================================================================
# Configuración DIAN
# ===========================================================================
# Ambiente con el que nace un emisor nuevo (2 = habilitación, 1 = producción).
# Ya no gobierna la emisión: contra qué servidor se emite lo dicen los campos
# `ambiente_facturacion` y `ambiente_nomina` del emisor, y el documento se lleva
# el valor al crearse y lo sella al firmar. Solo lo lee `ambiente_por_defecto()`
# de `apps.emisores.models.emisor`.
DIAN_ENVIRONMENT = env.int("DIAN_ENVIRONMENT", default=2)

# Endpoints de los Web Services de la DIAN por ambiente.
DIAN_WSDL = {
    # Habilitación
    2: env(
        "DIAN_WSDL_HABILITACION",
        default="https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl",
    ),
    # Producción
    1: env(
        "DIAN_WSDL_PRODUCCION",
        default="https://vpfe.dian.gov.co/WcfDianCustomerServices.svc?wsdl",
    ),
}

# Identificador de la política de firma DIAN (XAdES-EPES).
DIAN_POLICY_ID = env(
    "DIAN_POLICY_ID",
    default="https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf",
)
DIAN_POLICY_NAME = env(
    "DIAN_POLICY_NAME",
    default="Política de firma para facturas electrónicas de la República de Colombia.",
)
# La nómina lleva su propio texto en xades:SigPolicyId/xades:Description: el
# anexo de nómina lo fija en el numeral 7.10 y dice "nóminas" donde el de
# factura dice "facturas". Es la misma política —mismo Identifier y mismo
# SigPolicyHash—, solo cambia la descripción.
DIAN_POLICY_NAME_NOMINA = env(
    "DIAN_POLICY_NAME_NOMINA",
    default="Política de firma para nóminas electrónicas de la República de Colombia.",
)
# Hash (SHA-256 en base64) del PDF de la política de firma DIAN. Es el valor que
# va en xades:SigPolicyHash/ds:DigestValue. Debe corresponder al PDF de
# DIAN_POLICY_ID; calcularlo con apps.dian.firma.calcular_hash_politica().
DIAN_POLICY_HASH = env(
    "DIAN_POLICY_HASH",
    default="dMoMvtcG5aIzgYo0tIsSQeVJBDnUnfSOfBpxXrmor0Y=",
)

# --- Fabricante del software (documento equivalente) -------------------------
# La extensión `InformacionDelFabricanteDelSoftware` del tiquete P.O.S. describe
# a **quien hizo el software**, no a quien emite, así que es de la plataforma y
# no del emisor: los tiquetes de cualquier cliente dicen lo mismo. Estuvieron
# como campos de `SoftwareDian` y se movieron aquí el 2026-09-01 al ver que
# rellenarlos por emisor invitaba a copiar ahí la razón social del emisor —que
# es lo que hace el XML mentir sobre quién fabricó el software, y solo se nota
# con el segundo cliente—.
#
# `SoftwareDian` conserva los tres campos como **excepción por emisor**: la DIAN
# admite que un obligado use software propio en vez del de un proveedor
# tecnológico, y ese sí tiene otro fabricante. Vacíos, mandan estos.
#
# Las reglas DEAB41 a DEAB46 son de rechazo: si los tres salen vacíos, la DIAN
# devuelve el documento.
DIAN_FABRICANTE_NOMBRE = env(
    "DIAN_FABRICANTE_NOMBRE", default="Mario A. Estrada",
)
DIAN_FABRICANTE_RAZON_SOCIAL = env(
    "DIAN_FABRICANTE_RAZON_SOCIAL", default="Semantica Digital S.A.S",
)
DIAN_FABRICANTE_NOMBRE_SOFTWARE = env(
    "DIAN_FABRICANTE_NOMBRE_SOFTWARE", default="RedEDoc",
)

# Carpeta donde se almacenan los XML/PDF generados (relativa a MEDIA_ROOT).
DIAN_STORAGE_SUBDIR = "dian"

# Carpeta con las listas de valores oficiales DIAN en formato Genericode (.gc).
CATALOGOS_LISTAS_DIR = BASE_DIR / "apps" / "catalogos" / "datos" / "listas"

# Carpeta con los esquemas XSD oficiales DIAN (validación del XML UBL).
DIAN_XSD_DIR = BASE_DIR / "apps" / "dian" / "datos" / "xsd"
