"""ViewSet del catálogo tipo de organización."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoOrganizacionViewSet(_CatalogoViewSet):
    queryset = models.TipoOrganizacion.objects.all()
