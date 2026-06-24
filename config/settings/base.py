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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
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
    "PAGE_SIZE": 50,
}

# --- JWT (frontend SPA) -----------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_MINUTOS", default=30)
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
# Ambiente de operación frente a la DIAN:
#   2 = Habilitación / Set de Pruebas
#   1 = Producción
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
# Hash (SHA-256 en base64) del PDF de la política de firma DIAN. Es el valor que
# va en xades:SigPolicyHash/ds:DigestValue. Debe corresponder al PDF de
# DIAN_POLICY_ID; calcularlo con apps.dian.firma.calcular_hash_politica().
DIAN_POLICY_HASH = env("DIAN_POLICY_HASH", default="")

# Carpeta donde se almacenan los XML/PDF generados (relativa a MEDIA_ROOT).
DIAN_STORAGE_SUBDIR = "dian"

# Carpeta con las listas de valores oficiales DIAN en formato Genericode (.gc).
CATALOGOS_LISTAS_DIR = BASE_DIR / "apps" / "catalogos" / "datos" / "listas"

# Carpeta con los esquemas XSD oficiales DIAN (validación del XML UBL).
DIAN_XSD_DIR = BASE_DIR / "apps" / "dian" / "datos" / "xsd"
