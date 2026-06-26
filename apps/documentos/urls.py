"""Rutas de la app documentos. Montadas bajo /api/documentos/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.documentos import views

router = SimpleRouter()
router.register("adquirente", views.AdquirenteViewSet)
router.register("documento", views.DocumentoElectronicoViewSet)

urlpatterns = router.urls
