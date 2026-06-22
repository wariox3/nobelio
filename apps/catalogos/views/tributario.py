"""ViewSets de catálogos tributarios."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TributoViewSet(_CatalogoViewSet):
    queryset = models.Tributo.objects.all()
