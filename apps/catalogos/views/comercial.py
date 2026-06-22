"""ViewSets de catálogos de uso comercial (unidades, pagos, moneda)."""
from apps.catalogos import models

from .base import _CatalogoViewSet


class UnidadMedidaViewSet(_CatalogoViewSet):
    queryset = models.UnidadMedida.objects.all()


class FormaPagoViewSet(_CatalogoViewSet):
    queryset = models.FormaPago.objects.all()


class MedioPagoViewSet(_CatalogoViewSet):
    queryset = models.MedioPago.objects.all()


class MonedaViewSet(_CatalogoViewSet):
    queryset = models.Moneda.objects.all()
