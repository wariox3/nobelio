"""Rutas de la app emisores. Montadas bajo /api/emisores/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.emisores import views

router = SimpleRouter()
router.register("emisor", views.EmisorViewSet)
router.register("software", views.SoftwareDianViewSet)
router.register("certificado", views.CertificadoViewSet)
router.register("resolucion", views.ResolucionFacturacionViewSet)

urlpatterns = router.urls
