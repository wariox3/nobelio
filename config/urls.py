"""Configuración de URLs del proyecto Nobelio.

Cada app gestiona sus propias rutas en su `urls.py`; aquí solo se montan bajo el
prefijo de su dominio.
"""
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.permissions import AllowAny

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def estado_servicio(_request):
    """Endpoint simple de verificación de estado del servicio."""
    return JsonResponse({"servicio": "nobelio", "estado": "ok"})


urlpatterns = [
    path("estado/", estado_servicio, name="estado-servicio"),

    # --- Documentación de la API ------------------------------------------
    # Públicas a propósito: el esquema lo consumen el frontend y quien integre
    # un ERP, y no revela nada que no esté ya en las rutas. Las tres son
    # `AllowAny` de forma explícita porque el permiso por defecto del proyecto
    # es `IsAuthenticated`, y sin esto la documentación pediría credenciales.
    #
    # El mismo esquema, versionado, vive en `schema.yml` (ver
    # `config/tests_esquema.py`): es lo que se entrega al integrador para que
    # genere su cliente sin depender de que el servidor esté arriba.
    path("api/schema/", SpectacularAPIView.as_view(
        permission_classes=[AllowAny], authentication_classes=[],
    ), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(
        url_name="schema", permission_classes=[AllowAny],
        authentication_classes=[],
    ), name="docs"),
    path("api/redoc/", SpectacularRedocView.as_view(
        url_name="schema", permission_classes=[AllowAny],
        authentication_classes=[],
    ), name="redoc"),

    path("api/seguridad/", include("apps.seguridad.urls")),
    path("api/catalogos/", include("apps.catalogos.urls")),
    path("api/emisores/", include("apps.emisores.urls")),
    path("api/documentos/", include("apps.documentos.urls")),
    path("api/nomina/", include("apps.nomina.urls")),
]
