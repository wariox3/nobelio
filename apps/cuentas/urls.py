"""Rutas de la app cuentas. Montadas bajo /api/cuentas/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.cuentas import views

router = SimpleRouter()
router.register("cuenta", views.CuentaViewSet)

urlpatterns = router.urls
