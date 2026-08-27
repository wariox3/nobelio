"""ViewSet del catálogo tipo de contrato."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class TipoContratoViewSet(_CatalogoViewSet):
    queryset = models.TipoContrato.objects.all()
