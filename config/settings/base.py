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
    "corsheaders",
]

LOCAL_APPS = [
    "apps.nucleo",
    "apps.cuentas",
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
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
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

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
    # Errores con cuerpo homogéneo: {"detail": ..., "errores": {...}}.
    "EXCEPTION_HANDLER": "apps.nucleo.api.exception_handler",
    # Dos vías coexistiendo: API Key (ERP) y JWT (frontend SPA). Ver
    # docs/autenticacion.md. Ambas son stateless (sin sesión, sin CSRF).
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.seguridad.autenticacion.LlaveApiAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
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
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("THROTTLE_USUARIO", default="300/hour"),
        "anon": env("THROTTLE_ANONIMO", default="30/hour"),
    },
}

# --- JWT (frontend SPA) -----------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_MINUTOS", default=720)  # 12 horas
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DIAS", default=7)),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

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
