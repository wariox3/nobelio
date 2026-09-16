"""Rutas de la app documentos. Montadas bajo /api/documentos/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.documentos import views

router = SimpleRouter()
router.register("documento", views.DocumentoViewSet)
router.register("documento-evento", views.DocumentoEventoViewSet)

urlpatterns = router.urls
