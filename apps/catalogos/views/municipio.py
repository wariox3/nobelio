"""ViewSet del catálogo municipio (incluye su departamento)."""
from rest_framework import viewsets

from apps.catalogos import models, serializers


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Municipio.objects.select_related("departamento")
    serializer_class = serializers.MunicipioSerializer
    search_fields = ["codigo", "nombre"]
