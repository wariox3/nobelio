"""API de catálogos DIAN (solo lectura)."""
from apps.catalogos.esquema import documentar

from .base import _CatalogoViewSet
from .departamento import DepartamentoViewSet
from .forma_pago import FormaPagoViewSet
from .indice import CatalogosView
from .medio_pago import MedioPagoViewSet
from .moneda import MonedaViewSet
from .municipio import MunicipioViewSet
from .pais import PaisViewSet
from .periodo_nomina import PeriodoNominaViewSet
from .responsabilidad_fiscal import ResponsabilidadFiscalViewSet
from .subtipo_trabajador import SubTipoTrabajadorViewSet
from .tipo_contrato import TipoContratoViewSet
from .tipo_factura import TipoFacturaViewSet
from .tipo_identificacion import TipoIdentificacionViewSet
from .tipo_organizacion import TipoOrganizacionViewSet
from .tipo_trabajador import TipoTrabajadorViewSet
from .tributo import TributoViewSet
from .unidad_medida import UnidadMedidaViewSet

# Cada ViewSet se describe en el esquema con la ficha de su catálogo.
for _vista in (
    DepartamentoViewSet, FormaPagoViewSet, MedioPagoViewSet, MonedaViewSet,
    MunicipioViewSet, PaisViewSet, PeriodoNominaViewSet,
    ResponsabilidadFiscalViewSet, SubTipoTrabajadorViewSet, TipoContratoViewSet,
    TipoFacturaViewSet, TipoIdentificacionViewSet, TipoOrganizacionViewSet,
    TipoTrabajadorViewSet, TributoViewSet, UnidadMedidaViewSet,
):
    documentar(_vista)

__all__ = [
    "CatalogosView",
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
    # Nómina electrónica.
    "PeriodoNominaViewSet",
    "TipoContratoViewSet",
    "TipoTrabajadorViewSet",
    "SubTipoTrabajadorViewSet",
]
