"""ViewSet del catálogo forma de pago."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class FormaPagoViewSet(_CatalogoViewSet):
    queryset = models.FormaPago.objects.all()
