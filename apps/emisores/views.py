"""API de emisores."""
from rest_framework import viewsets

from . import models, serializers


class EmisorViewSet(viewsets.ModelViewSet):
    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion", "nombre_comercial"]


class ResolucionFacturacionViewSet(viewsets.ModelViewSet):
    queryset = models.ResolucionFacturacion.objects.select_related("emisor", "tipo_factura")
    serializer_class = serializers.ResolucionFacturacionSerializer
