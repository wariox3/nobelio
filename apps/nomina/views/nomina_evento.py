"""API de los eventos de las nóminas."""
from rest_framework import viewsets

from apps.nomina import serializers
from apps.nomina.models import NominaEvento
from apps.nucleo.api import uuid_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin


class NominaEventoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los cambios de estado de las nóminas ante la DIAN.

    Solo cambios de estado —firmada, enviada sin veredicto, validada,
    rechazada—: crear no deja evento, ni una consulta que no cambia nada. De
    solo lectura: los eventos los escribe el sistema al emitir.

    Filtros: ``?nomina=<uuid>`` y ``?tipo=firmado|enviado|validado|rechazado``.
    """

    serializer_class = serializers.NominaEventoSerializer
    queryset = NominaEvento.objects.all()
    # Solo se ven los eventos de nóminas de emisores del alcance.
    campo_emisor = "nomina__emisor"

    def get_queryset(self):
        qs = super().get_queryset()
        nomina = uuid_de_query(self.request.query_params, "nomina")
        if nomina:
            qs = qs.filter(nomina=nomina)
        tipo = self.request.query_params.get("tipo")
        if tipo:
            qs = qs.filter(tipo=tipo)
        return qs
