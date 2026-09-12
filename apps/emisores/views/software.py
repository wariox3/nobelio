"""API del software DIAN del emisor."""
from django.db import IntegrityError, transaction
from rest_framework import viewsets

from apps.emisores import models, serializers
from apps.emisores.servicios import (
    sembrar_documentos_de_prueba,
    sembrar_resolucion_de_pruebas,
)
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
        """Registra el software y le siembra la resolución del Set de Pruebas.

        Registrar el software es el primer paso de una habilitación y sin
        numeración no hay nada que emitir después: dejar los dos pasos
        separados deja al emisor con software y sin poder crear ni un documento
        de prueba, sin que nada lo diga.

        Qué se siembra lo decide el tipo de software, y puede no ser nada: la
        nómina no se numera con resolución y la del documento equivalente
        todavía no se conoce. Tampoco se siembra si el emisor ya está en
        producción para esa operación. Ver `sembrar_resolucion_de_pruebas`.

        Con la resolución puesta, el de facturación deja además sus facturas de
        prueba en borrador. Son el material del Set de Pruebas y se crean aquí
        por lo mismo que la resolución: para que el emisor no acabe el alta con
        numeración y sin nada que emitir contra ella.

        Va dentro de la misma transacción que el alta: media habilitación es
        peor que ninguna, porque no se ve.
        """
        with transaction.atomic():
            self._guardar(super().perform_create, serializer)
            software = serializer.instance
            resolucion, _ = sembrar_resolucion_de_pruebas(
                software.emisor, software.tipo,
            )
            sembrar_documentos_de_prueba(
                software.emisor, software.tipo, resolucion,
            )

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
