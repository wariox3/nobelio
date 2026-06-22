"""API de catálogos DIAN (solo lectura)."""
from .base import _CatalogoViewSet
from .comercial import (
    FormaPagoViewSet,
    MedioPagoViewSet,
    MonedaViewSet,
    UnidadMedidaViewSet,
)
from .documento import TipoFacturaViewSet
from .geografia import DepartamentoViewSet, MunicipioViewSet, PaisViewSet
from .tercero import (
    ResponsabilidadFiscalViewSet,
    TipoIdentificacionViewSet,
    TipoOrganizacionViewSet,
)
from .tributario import TributoViewSet

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
