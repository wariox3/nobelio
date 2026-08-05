"""API de adquirientes."""
from rest_framework import viewsets

from apps.documentos import serializers
from apps.documentos.models import Adquiriente
from apps.seguridad.alcance import AlcanceEmisorMixin


class AdquirienteViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """CRUD de adquirientes, acotado a los emisores del solicitante.

    La cartera de clientes es de cada emisor: una integración solo ve la de los
    emisores de su propia cuenta.
    """

    queryset = Adquiriente.objects.select_related("emisor")
    serializer_class = serializers.AdquirienteSerializer
    search_fields = ["razon_social", "numero_identificacion"]

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/documentos/adquiriente/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = self.request.query_params.get("emisor")
        return qs.filter(emisor=emisor) if emisor else qs
