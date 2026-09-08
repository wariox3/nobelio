"""Rutas de la app seguridad. Montadas bajo /api/seguridad/ en config/urls.py."""
from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.seguridad import views

router = SimpleRouter()
router.register("usuario", views.UsuarioViewSet)
router.register("llave-api", views.LlaveApiViewSet)

urlpatterns = [
    # --- Alta pública. Las únicas rutas anónimas: todo lo demás pide sesión.
    path("registro/", views.RegistroView.as_view(), name="registro"),
    path("registro/verificar/", views.VerificarView.as_view(),
         name="registro-verificar"),
    path("registro/reenviar/", views.ReenviarView.as_view(),
         name="registro-reenviar"),

    # --- Sesión. `token/` no siempre entrega la sesión: si la cuenta tiene
    # segundo factor, responde `mfa_token` y la emite `token/mfa/`.
    path("token/", views.SesionView.as_view(), name="token_obtain_pair"),
    path("token/mfa/", views.SesionMfaView.as_view(), name="token-mfa"),
    path("token/mfa/reenviar/", views.SesionMfaReenviarView.as_view(),
         name="token-mfa-reenviar"),
    path("token/refresh/", views.RefrescoView.as_view(), name="token_refresh"),
    path("token/cerrar/", views.CierreSesionView.as_view(), name="token-cerrar"),
    path("me/", views.MeView.as_view(), name="me"),

    # --- Segundo factor, siempre sobre la propia cuenta.
    path("mfa/metodos/", views.MfaMetodosView.as_view(), name="mfa-metodos"),
    path("mfa/", views.MfaEstadoView.as_view(), name="mfa-estado"),
    path("mfa/enrolar/", views.MfaEnrolarView.as_view(), name="mfa-enrolar"),
    path("mfa/confirmar/", views.MfaConfirmarView.as_view(), name="mfa-confirmar"),
    path("mfa/desactivar/", views.MfaDesactivarView.as_view(),
         name="mfa-desactivar"),
    path("mfa/codigos-respaldo/", views.MfaCodigosRespaldoView.as_view(),
         name="mfa-codigos-respaldo"),

    *router.urls,
]
