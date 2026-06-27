"""API del software DIAN del emisor."""
from rest_framework import viewsets

from apps.emisores import models, serializers


class SoftwareDianViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.SoftwareDianSerializer
    queryset = models.SoftwareDian.objects.select_related("emisor")

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/emisores/software/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = self.request.query_params.get("emisor")
        return qs.filter(emisor=emisor) if emisor else qs
