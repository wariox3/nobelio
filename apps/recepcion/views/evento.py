"""API de los eventos RADIAN de las facturas recibidas."""
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.nucleo.api import (
    ErrorSolicitud, OrdenEstable, PaginacionAjustable, entero_de_query, uuid_de_query,
)
from apps.nucleo.esquema import ErrorSerializer
from apps.recepcion import eventos, serializers
from apps.recepcion.models import EstadoEvento, Evento
from apps.seguridad.alcance import AlcanceEmisorMixin

RESPUESTA_XML = {(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer}


class EventoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los eventos RADIAN que los emisores registraron sobre sus facturas recibidas.

    Se piden con ``POST /api/recepcion/documento/{id}/evento/`` (o salen solos:
    el acuse 030 automático) y se envían a la DIAN en segundo plano. Aquí se
    consultan: ``estado`` dice si la DIAN lo registró (``registrado``), lo
    rechazó (``rechazado``, con el motivo en ``respuesta_descripcion``) o no se
    pudo enviar (``error``).

    Filtros: ``?documento=<uuid>``, ``?emisor=<id>``, ``?estado=`` y
    ``?codigo=030|031|032|033``. ``?ordering=`` por ``creado_en`` o ``numero``.

    ``POST {id}/enviar/`` reenvía uno en ``error``, con el mismo XML.
    ``xml/`` baja el evento firmado y ``respuesta/`` el ApplicationResponse
    con el que la DIAN respondió.
    """

    serializer_class = serializers.EventoSerializer
    queryset = Evento.objects.select_related("evento_radian", "concepto_reclamo")
    pagination_class = PaginacionAjustable
    filter_backends = [OrdenEstable]
    ordering_fields = ["creado_en", "numero"]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if documento := uuid_de_query(params, "documento"):
            qs = qs.filter(documento=documento)
        if (emisor := entero_de_query(params, "emisor")) is not None:
            qs = qs.filter(emisor=emisor)
        if estado := params.get("estado"):
            qs = qs.filter(estado=estado)
        if codigo := params.get("codigo"):
            qs = qs.filter(evento_radian__codigo=codigo)
        return qs

    @extend_schema(request=None, responses={200: serializers.EventoSerializer, 400: ErrorSerializer})
    @action(detail=True, methods=["post"])
    def enviar(self, request, pk=None):
        """Reenvía a la DIAN un evento en ``error``, ahora mismo.

        Con el mismo XML y el mismo CUDE. Si vuelve a fallar, queda en
        ``error`` con el detalle y responde 200 igual: lo que se pidió
        —enviarlo— se intentó.
        """
        evento = self.get_object()
        if evento.estado != EstadoEvento.ERROR:
            raise ErrorSolicitud(
                f"Solo se reenvían los eventos en error; este está "
                f"{evento.get_estado_display().lower()}."
            )
        evento = Evento.objects.select_related("documento", "emisor", "evento_radian").get(
            pk=evento.pk,
        )
        try:
            eventos.enviar(evento)
        except eventos.ErrorTransitorio as error:
            eventos.marcar_error(evento, str(error))
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(responses=RESPUESTA_XML)
    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        """El evento firmado, tal como se envió a la DIAN."""
        evento = self.get_object()
        return _descarga(evento.xml_archivo, f"{evento.numero}.xml",
                         "El evento todavía no tiene XML: no se ha generado.")

    @extend_schema(responses=RESPUESTA_XML)
    @action(detail=True, methods=["get"])
    def respuesta(self, request, pk=None):
        """El ApplicationResponse con el que la DIAN respondió al evento."""
        evento = self.get_object()
        return _descarga(evento.respuesta_archivo, f"{evento.numero}-dian.xml",
                         "La DIAN no devolvió un ApplicationResponse para este evento.")


def _descarga(archivo, nombre, mensaje):
    if not archivo:
        raise ErrorSolicitud(mensaje)
    return FileResponse(
        archivo.open("rb"), content_type="application/xml", as_attachment=True, filename=nombre,
    )
