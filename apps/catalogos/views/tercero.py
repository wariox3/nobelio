"""ViewSets de catálogos relativos a terceros."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoIdentificacionViewSet(_CatalogoViewSet):
    queryset = models.TipoIdentificacion.objects.all()


class TipoOrganizacionViewSet(_CatalogoViewSet):
    queryset = models.TipoOrganizacion.objects.all()


class ResponsabilidadFiscalViewSet(_CatalogoViewSet):
    queryset = models.ResponsabilidadFiscal.objects.all()
