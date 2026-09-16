"""API de los eventos de los documentos."""
from rest_framework import viewsets

from apps.documentos import serializers
from apps.documentos.models import DocumentoEvento
from apps.nucleo.api import uuid_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class DocumentoEventoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los cambios de estado de los documentos ante la DIAN.

    Solo cambios de estado —firmado, enviado sin veredicto, validado,
    rechazado—: crear no deja evento, ni una consulta que no cambia nada. De
    solo lectura: los eventos los escribe el sistema al emitir.

    Filtros: ``?documento=<uuid>`` y ``?tipo=firmado|enviado|validado|rechazado``.
    """

    serializer_class = serializers.DocumentoEventoSerializer
    queryset = DocumentoEvento.objects.all()
    # Solo se ven los eventos de documentos de emisores del alcance.
    campo_emisor = "documento__emisor"

    def get_queryset(self):
        qs = super().get_queryset()
        documento = uuid_de_query(self.request.query_params, "documento")
        if documento:
            qs = qs.filter(documento=documento)
        tipo = self.request.query_params.get("tipo")
        if tipo:
            qs = qs.filter(tipo=tipo)
        return qs
