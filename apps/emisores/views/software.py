"""API del software DIAN del emisor."""
from django.db import IntegrityError, transaction
from rest_framework import viewsets

from apps.emisores import models, serializers
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class SoftwareDianViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    serializer_class = serializers.SoftwareDianSerializer
    queryset = models.SoftwareDian.objects.select_related("emisor")

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/emisores/software/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        return qs.filter(emisor=emisor) if emisor else qs

    # Un software por emisor y operación lo comprueba el serializer, que da el
    # mensaje bueno (con la ruta del que ya existe). Lo de aquí abajo es solo
    # para la carrera: dos altas simultáneas del mismo tipo pasan las dos por
    # esa comprobación y es el índice único quien para a la segunda. Sin esto
    # saldría como un 500.

    def perform_create(self, serializer):
        self._guardar(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._guardar(super().perform_update, serializer)

    @staticmethod
    def _guardar(guardar, serializer):
        try:
            # El savepoint es lo que permite seguir atendiendo la petición:
            # sin él la transacción queda marcada y la siguiente consulta
            # muere con un TransactionManagementError en vez de con su error.
            with transaction.atomic():
                guardar(serializer)
        except IntegrityError:
            raise ErrorSolicitud(
                "El emisor ya tiene un software DIAN de esa operación. Cada "
                "una admite uno: actualice el que hay en vez de registrar otro."
            )
