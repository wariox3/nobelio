"""ViewSet del catálogo periodo de nómina."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class PeriodoNominaViewSet(_CatalogoViewSet):
    queryset = models.PeriodoNomina.objects.all()
