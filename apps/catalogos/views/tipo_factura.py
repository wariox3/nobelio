"""ViewSet del catálogo tipo de factura."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoFacturaViewSet(_CatalogoViewSet):
    queryset = models.TipoFactura.objects.all()
