"""ViewSet del catálogo subtipo de trabajador."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class SubTipoTrabajadorViewSet(_CatalogoViewSet):
    queryset = models.SubTipoTrabajador.objects.all()
