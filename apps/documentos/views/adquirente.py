"""API de adquirentes."""
from rest_framework import viewsets

from apps.documentos import serializers
from apps.documentos.models import Adquirente


class AdquirenteViewSet(viewsets.ModelViewSet):
    queryset = Adquirente.objects.all()
    serializer_class = serializers.AdquirenteSerializer
    search_fields = ["razon_social", "numero_identificacion"]
