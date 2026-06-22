"""API de resoluciones de facturación."""
from rest_framework import viewsets

from apps.emisores import models, serializers


class ResolucionFacturacionViewSet(viewsets.ModelViewSet):
    queryset = models.ResolucionFacturacion.objects.select_related("emisor", "tipo_factura")
    serializer_class = serializers.ResolucionFacturacionSerializer
