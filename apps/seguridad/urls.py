"""Rutas de la app seguridad. Montadas bajo /api/seguridad/ en config/urls.py."""
from django.urls import path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.seguridad import views

router = SimpleRouter()
router.register("usuario", views.UsuarioViewSet)
router.register("llave-api", views.LlaveApiViewSet)

urlpatterns = [
    # Autenticación del frontend (JWT): login con email + contraseña.
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    *router.urls,
]
