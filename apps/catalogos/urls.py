"""Rutas de la app catálogos. Montadas bajo /api/catalogos/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.catalogos import views

router = SimpleRouter()
router.register("tipos-factura", views.TipoFacturaViewSet)
router.register("tipos-identificacion", views.TipoIdentificacionViewSet)
router.register("tipos-organizacion", views.TipoOrganizacionViewSet)
router.register("responsabilidades", views.ResponsabilidadFiscalViewSet)
router.register("tributos", views.TributoViewSet)
router.register("unidades-medida", views.UnidadMedidaViewSet)
router.register("formas-pago", views.FormaPagoViewSet)
router.register("medios-pago", views.MedioPagoViewSet)
router.register("monedas", views.MonedaViewSet)
router.register("paises", views.PaisViewSet)
router.register("departamentos", views.DepartamentoViewSet)
router.register("municipios", views.MunicipioViewSet)

urlpatterns = router.urls
