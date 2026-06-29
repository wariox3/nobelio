"""ViewSet del catálogo moneda."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class MonedaViewSet(_CatalogoViewSet):
    queryset = models.Moneda.objects.all()
