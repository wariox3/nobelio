"""ViewSet del catálogo eventos RADIAN."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class EventoRadianViewSet(_CatalogoViewSet):
    queryset = models.EventoRadian.objects.all()
