"""ViewSet del catálogo país."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class PaisViewSet(_CatalogoViewSet):
    queryset = models.Pais.objects.all()
