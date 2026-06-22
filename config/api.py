"""Router de la API REST de Nobelio."""
from rest_framework.routers import DefaultRouter

from apps.catalogos import views as catalogos
from apps.documentos import views as documentos
from apps.emisores import views as emisores
from apps.seguridad import views as seguridad

router = DefaultRouter()

# Seguridad (usuarios; solo staff/admin)
router.register("usuarios", seguridad.UsuarioViewSet)

# Catálogos (solo lectura)
router.register("catalogos/tipos-factura", catalogos.TipoFacturaViewSet)
router.register("catalogos/tipos-identificacion", catalogos.TipoIdentificacionViewSet)
router.register("catalogos/tipos-organizacion", catalogos.TipoOrganizacionViewSet)
router.register("catalogos/responsabilidades", catalogos.ResponsabilidadFiscalViewSet)
router.register("catalogos/tributos", catalogos.TributoViewSet)
router.register("catalogos/unidades-medida", catalogos.UnidadMedidaViewSet)
router.register("catalogos/formas-pago", catalogos.FormaPagoViewSet)
router.register("catalogos/medios-pago", catalogos.MedioPagoViewSet)
router.register("catalogos/monedas", catalogos.MonedaViewSet)
router.register("catalogos/paises", catalogos.PaisViewSet)
router.register("catalogos/departamentos", catalogos.DepartamentoViewSet)
router.register("catalogos/municipios", catalogos.MunicipioViewSet)

# Emisores
router.register("emisores", emisores.EmisorViewSet)
router.register("resoluciones", emisores.ResolucionFacturacionViewSet)

# Documentos
router.register("adquirentes", documentos.AdquirenteViewSet)
router.register("documentos", documentos.DocumentoElectronicoViewSet)
