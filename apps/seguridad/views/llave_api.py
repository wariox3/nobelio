"""API de gestión de llaves de API. Solo accesible para usuarios staff."""
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.seguridad.models import LlaveApi
from apps.seguridad.serializers import LlaveApiSerializer


class LlaveApiViewSet(viewsets.ModelViewSet):
    """CRUD de llaves de API.

    Las gestiona el frontend (usuarios staff vía JWT). El secreto completo solo
    se devuelve al crear la llave; para rotarla se crea una nueva y se desactiva
    o borra la anterior.
    """

    queryset = LlaveApi.objects.select_related("emisor").all()
    serializer_class = LlaveApiSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["nombre", "prefijo", "emisor__razon_social"]
