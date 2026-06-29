"""ViewSet del catálogo responsabilidad fiscal."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class ResponsabilidadFiscalViewSet(_CatalogoViewSet):
    queryset = models.ResponsabilidadFiscal.objects.all()
