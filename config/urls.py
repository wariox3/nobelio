"""Configuración de URLs del proyecto Nobelio.

Cada app gestiona sus propias rutas en su `urls.py`; aquí solo se montan bajo el
prefijo de su dominio.
"""
from django.http import JsonResponse
from django.urls import include, path


def estado_servicio(_request):
    """Endpoint simple de verificación de estado del servicio."""
    return JsonResponse({"servicio": "nobelio", "estado": "ok"})


urlpatterns = [
    path("estado/", estado_servicio, name="estado-servicio"),
    path("api/seguridad/", include("apps.seguridad.urls")),
    path("api/catalogos/", include("apps.catalogos.urls")),
    path("api/", include("apps.emisores.urls")),
    path("api/", include("apps.documentos.urls")),
]
