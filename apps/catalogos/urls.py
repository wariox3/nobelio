"""Rutas de la app catálogos. Montadas bajo /api/catalogos/ en config/urls.py."""
from rest_framework.routers import SimpleRouter

from apps.catalogos import views

router = SimpleRouter()
router.register("tipo-factura", views.TipoFacturaViewSet)
router.register("tipo-identificacion", views.TipoIdentificacionViewSet)
router.register("tipo-organizacion", views.TipoOrganizacionViewSet)
router.register("responsabilidad-fiscal", views.ResponsabilidadFiscalViewSet)
router.register("tributo", views.TributoViewSet)
router.register("unidad-medida", views.UnidadMedidaViewSet)
router.register("forma-pago", views.FormaPagoViewSet)
router.register("medio-pago", views.MedioPagoViewSet)
router.register("moneda", views.MonedaViewSet)
router.register("pais", views.PaisViewSet)
router.register("departamento", views.DepartamentoViewSet)
router.register("municipio", views.MunicipioViewSet)

urlpatterns = router.urls
