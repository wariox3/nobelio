"""ViewSet del catálogo municipio (incluye su departamento)."""
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from apps.catalogos import models, serializers


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Municipio.objects.select_related("departamento")
    serializer_class = serializers.MunicipioSerializer
    permission_classes = [AllowAny]
    search_fields = ["codigo", "nombre"]
