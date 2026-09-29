"""ViewSet del catálogo municipio (incluye su departamento)."""
from apps.catalogos import models, serializers

from .base import _CatalogoViewSet


class MunicipioViewSet(_CatalogoViewSet):
    queryset = models.Municipio.objects.select_related("departamento")
    serializer_class = serializers.MunicipioSerializer
