"""API de catálogos DIAN (solo lectura)."""
from .base import _CatalogoViewSet
from .departamento import DepartamentoViewSet
from .forma_pago import FormaPagoViewSet
from .medio_pago import MedioPagoViewSet
from .moneda import MonedaViewSet
from .municipio import MunicipioViewSet
from .pais import PaisViewSet
from .responsabilidad_fiscal import ResponsabilidadFiscalViewSet
from .tipo_factura import TipoFacturaViewSet
from .tipo_identificacion import TipoIdentificacionViewSet
from .tipo_organizacion import TipoOrganizacionViewSet
from .tributo import TributoViewSet
from .unidad_medida import UnidadMedidaViewSet

__all__ = [
    "TipoFacturaViewSet",
    "TipoIdentificacionViewSet",
    "TipoOrganizacionViewSet",
    "ResponsabilidadFiscalViewSet",
    "TributoViewSet",
    "UnidadMedidaViewSet",
    "FormaPagoViewSet",
    "MedioPagoViewSet",
    "MonedaViewSet",
    "PaisViewSet",
    "DepartamentoViewSet",
    "MunicipioViewSet",
]
