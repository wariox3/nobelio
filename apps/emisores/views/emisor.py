"""API del emisor."""
from rest_framework import viewsets

from apps.emisores import models, serializers


class EmisorViewSet(viewsets.ModelViewSet):
    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion", "nombre_comercial"]
