"""Configuración de URLs del proyecto Nobelio."""
from django.http import JsonResponse
from django.urls import include, path

from config.api import router


def estado_servicio(_request):
    """Endpoint simple de verificación de estado del servicio."""
    return JsonResponse({"servicio": "nobelio", "estado": "ok"})


urlpatterns = [
    path("estado/", estado_servicio, name="estado-servicio"),
    path("api/", include(router.urls)),
    path("api/auth/", include("rest_framework.urls")),
]
