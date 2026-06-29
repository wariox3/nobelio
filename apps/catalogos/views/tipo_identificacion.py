"""ViewSet del catálogo tipo de identificación."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoIdentificacionViewSet(_CatalogoViewSet):
    queryset = models.TipoIdentificacion.objects.all()
