"""API del software DIAN del emisor."""
from django.db import IntegrityError, transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.emisores.servicios import (
    crear_nomina_de_prueba,
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

    @action(detail=True, methods=["post"], url_path="crear-nomina-prueba")
    def crear_nomina_prueba(self, request, pk=None):
        """Crea una nómina de prueba en borrador para este software.

        ``POST /api/emisores/software/{id}/crear-nomina-prueba/``, con
        ``{"consecutivo": <n>}`` opcional.

        Es la misma que siembra el alta, pero de una en una: sirve para
        completar un Set de Pruebas al que le faltan nóminas. **Solo sobre un
        software de nómina**; los documentos de prueba de facturación se crean
        desde su resolución, que es quien los numera.

        Sin ``consecutivo`` toma el siguiente libre del emisor para el prefijo
        de pruebas. El periodo de liquidación continúa la serie hacia atrás —la
        regla 90 rechaza dos nóminas del mismo trabajador para el mismo
        periodo—; si hace falta otro, se ajusta en el borrador con un ``PATCH``.

        Solo la crea: no la firma ni la envía. Para eso están ``emitir`` y
        ``enviar`` de ``/api/nomina/nomina/{id}/``.
        """
        # `get_object` va contra el queryset del mixin, así que un software
        # fuera del alcance no se encuentra (404) en vez de responder 403 y
        # delatar que existe.
        software = self.get_object()

        consecutivo = request.data.get("consecutivo")
        if consecutivo in (None, ""):
            consecutivo = None
        else:
            try:
                consecutivo = int(consecutivo)
            except (TypeError, ValueError):
                raise ErrorSolicitud("El consecutivo debe ser un número entero.")
            if consecutivo < 1:
                raise ErrorSolicitud("El consecutivo debe ser mayor que cero.")

        try:
            nomina = crear_nomina_de_prueba(software, consecutivo)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))

        return Response(
            {
                "id": str(nomina.id),
                "numero": nomina.numero,
                "consecutivo": nomina.consecutivo,
                "estado": nomina.estado.nombre,
                "periodo": [
                    str(nomina.fecha_liquidacion_inicio),
                    str(nomina.fecha_liquidacion_fin),
                ],
            },
            status=status.HTTP_201_CREATED,
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
