"""Settings de desarrollo local."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env("DEBUG", default=True)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# En dev, mostramos también el navegable de DRF y permitimos sin auth para iterar.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
