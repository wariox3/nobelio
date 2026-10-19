"""API de los adjuntos de los correos de recepción."""
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action

from apps.nucleo.api import ErrorSolicitud, entero_de_query, uuid_de_query
from apps.recepcion import serializers
from apps.recepcion.models import Adjunto
from apps.seguridad.alcance import AlcanceEmisorMixin, emisores_permitidos


def adjuntos_visibles(request):
    """Los adjuntos que alcanza quien pide, por su ``emisor``.

    Los de un correo sin emisor (``emisor`` nulo) solo los ve quien alcanza
    todos.
    """
    qs = Adjunto.objects.all()
    permitidos = emisores_permitidos(request)
    if permitidos is None:
        return qs
    return qs.filter(emisor__in=permitidos)


def descarga(adjunto):
    """Stream desde B2 con el nombre original. Siempre como descarga, nunca
    para mostrar: un HTML o un SVG de un tercero no se abre en el navegador
    con el origen de la API."""
    return FileResponse(
        adjunto.archivo.open("rb"), content_type=adjunto.tipo_contenido,
        as_attachment=True, filename=adjunto.nombre,
    )


class AdjuntoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Los archivos de los correos de recepción: todo lo que trajeron, ya fuera
    de sus ZIP.

    De solo lectura: los crea el procesamiento del correo y se borran con él.

    El listado exige ``?emisor=<id>``: sin él responde 400. Cada uno ve solo
    los adjuntos de los emisores que alcanza.

    Filtros: ``?correo=<id>``, ``?documento=<uuid>`` y
    ``?rol=xml|xml_documento|pdf|otro``. ``?search=`` busca en el nombre
    original. El contenido se baja con ``descargar/``.
    """

    serializer_class = serializers.AdjuntoSerializer
    queryset = Adjunto.objects.order_by("-creado_en")
    filter_backends = [filters.SearchFilter]
    search_fields = ["nombre"]

    def get_queryset(self):
        # El mixin ya acotó a los emisores que alcanza quien pide.
        qs = super().get_queryset()
        params = self.request.query_params
        if (emisor := entero_de_query(params, "emisor")) is not None:
            qs = qs.filter(emisor=emisor)
        if (correo := entero_de_query(params, "correo")) is not None:
            qs = qs.filter(correo=correo)
        if documento := uuid_de_query(params, "documento"):
            qs = qs.filter(documento=documento)
        if rol := params.get("rol"):
            qs = qs.filter(rol=rol)
        return qs

    @extend_schema(parameters=[OpenApiParameter(
        "emisor", int, required=True, description="Id del emisor. Obligatorio.",
    )])
    def list(self, request, *args, **kwargs):
        if entero_de_query(request.query_params, "emisor") is None:
            raise ErrorSolicitud("El filtro 'emisor' es obligatorio.")
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def descargar(self, request, pk=None):
        """El archivo, con su nombre original."""
        return descarga(self.get_object())
