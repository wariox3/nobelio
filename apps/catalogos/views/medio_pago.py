"""ViewSet del catálogo medio de pago."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class MedioPagoViewSet(_CatalogoViewSet):
    queryset = models.MedioPago.objects.all()
