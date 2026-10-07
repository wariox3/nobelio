"""API de las cuentas (agrupadores de emisores)."""
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.emisores import models, serializers
from apps.nucleo.api import ErrorSolicitud

MENSAJE_CUENTA_CON_EMISORES = (
    "La cuenta tiene emisores. Sácalos de la cuenta antes de borrarla."
)


class CuentaViewSet(viewsets.ModelViewSet):
    """CRUD de cuentas. Solo staff: las cuentas son de toda la plataforma y
    listarlas revelaría qué integraciones la usan.

    Una cuenta con emisores no se puede borrar (400): hay que sacarlos antes.
    """

    queryset = models.Cuenta.objects.all()
    serializer_class = serializers.CuentaSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["nombre"]

    def perform_destroy(self, instance):
        # Comprobado antes de borrar y no capturando el ProtectedError: así el
        # mensaje dice qué hacer, en vez de un 500.
        if instance.emisores.exists():
            raise ErrorSolicitud(MENSAJE_CUENTA_CON_EMISORES)
        instance.delete()
