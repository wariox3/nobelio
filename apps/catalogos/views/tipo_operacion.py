"""ViewSet del catálogo tipos de operación."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoOperacionViewSet(_CatalogoViewSet):
    queryset = models.TipoOperacion.objects.all()
