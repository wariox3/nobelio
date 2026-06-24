"""Rutas de la app emisores. Montadas bajo /api/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.emisores import views

router = SimpleRouter()
router.register("emisores", views.EmisorViewSet)
router.register("resoluciones", views.ResolucionFacturacionViewSet)

urlpatterns = router.urls
