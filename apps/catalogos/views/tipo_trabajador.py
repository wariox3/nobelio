"""ViewSet del catálogo tipo de trabajador."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoTrabajadorViewSet(_CatalogoViewSet):
    queryset = models.TipoTrabajador.objects.all()
