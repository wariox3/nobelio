"""Rutas de la app nómina. Montadas bajo /api/nomina/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.nomina import views

router = SimpleRouter()
router.register("empleado", views.EmpleadoViewSet)
router.register("nomina", views.NominaViewSet)

urlpatterns = router.urls
