"""ViewSet del catálogo unidad de medida."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class UnidadMedidaViewSet(_CatalogoViewSet):
    queryset = models.UnidadMedida.objects.all()
