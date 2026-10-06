"""ViewSet del catálogo conceptos de reclamo."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class ConceptoReclamoViewSet(_CatalogoViewSet):
    queryset = models.ConceptoReclamo.objects.all()
