"""ViewSet del catálogo departamento."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class DepartamentoViewSet(_CatalogoViewSet):
    queryset = models.Departamento.objects.all()
