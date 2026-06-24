"""Rutas de la app documentos. Montadas bajo /api/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.documentos import views

router = SimpleRouter()
router.register("adquirentes", views.AdquirenteViewSet)
router.register("documentos", views.DocumentoElectronicoViewSet)

urlpatterns = router.urls
