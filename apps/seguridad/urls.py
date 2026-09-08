"""Rutas de la app seguridad. Montadas bajo /api/seguridad/ en config/urls.py."""
from django.urls import path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from apps.seguridad import views

router = SimpleRouter()
router.register("usuario", views.UsuarioViewSet)
router.register("llave-api", views.LlaveApiViewSet)

urlpatterns = [
    # Alta pública. Son las únicas rutas anónimas de la app: todo lo demás
    # exige credencial.
    path("registro/", views.RegistroView.as_view(), name="registro"),
    path(
        "registro/verificar/",
        views.VerificarView.as_view(),
        name="registro-verificar",
    ),
    path(
        "registro/reenviar/",
        views.ReenviarView.as_view(),
        name="registro-reenviar",
    ),
    # Autenticación del frontend (JWT): login con email + contraseña. El
    # serializer propio es el que exige, además, el correo confirmado.
    path("token/", views.TokenView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    *router.urls,
]
