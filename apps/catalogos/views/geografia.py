"""ViewSets de catálogos geográficos."""
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from apps.catalogos import models, serializers

from .base import _CatalogoViewSet


class PaisViewSet(_CatalogoViewSet):
    queryset = models.Pais.objects.all()


class DepartamentoViewSet(_CatalogoViewSet):
    queryset = models.Departamento.objects.all()


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Municipio.objects.select_related("departamento")
    serializer_class = serializers.MunicipioSerializer
    permission_classes = [AllowAny]
    search_fields = ["codigo", "nombre"]
