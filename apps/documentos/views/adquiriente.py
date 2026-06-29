"""API de adquirientes."""
from rest_framework import viewsets

from apps.documentos import serializers
from apps.documentos.models import Adquiriente


class AdquirienteViewSet(viewsets.ModelViewSet):
    queryset = Adquiriente.objects.all()
    serializer_class = serializers.AdquirienteSerializer
