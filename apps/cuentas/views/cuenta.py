"""API de cuentas (clientes/tenants). Solo accesible para usuarios staff."""
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.cuentas.models import Cuenta
from apps.cuentas.serializers import CuentaSerializer


class CuentaViewSet(viewsets.ModelViewSet):
    """CRUD de cuentas. La gestiona el staff de la plataforma."""

    queryset = Cuenta.objects.all()
    serializer_class = CuentaSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["nombre", "identificacion", "correo_contacto"]
