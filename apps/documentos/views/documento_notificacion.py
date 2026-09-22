"""API de las notificaciones de los documentos."""
from rest_framework import viewsets

from apps.documentos import serializers
from apps.documentos.models import DocumentoNotificacion
from apps.nucleo.api import uuid_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class DocumentoNotificacionViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los envíos de los documentos al adquiriente por correo.

    Uno por cada intento que llegó a la pasarela, haya salido o no: a quién se
    mandó, qué llevaba el zip, el código con el que se rastrea en Zinc y, si
    falló, por qué. De solo lectura: las escribe la acción `notificar`.

    Filtros: ``?documento=<uuid>`` y ``?estado=enviado|fallido``.
    """

    serializer_class = serializers.DocumentoNotificacionSerializer
    queryset = DocumentoNotificacion.objects.all()
    # Solo se ven las de documentos de emisores del alcance.
    campo_emisor = "documento__emisor"

    def get_queryset(self):
        qs = super().get_queryset()
        documento = uuid_de_query(self.request.query_params, "documento")
        if documento:
            qs = qs.filter(documento=documento)
        estado = self.request.query_params.get("estado")
        if estado:
            qs = qs.filter(estado=estado)
        return qs
