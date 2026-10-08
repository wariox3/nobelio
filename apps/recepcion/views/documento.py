"""API de los documentos recibidos de proveedores."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from django.db.models import Exists, OuterRef
from rest_framework import filters, viewsets
from rest_framework.decorators import action

from apps.nucleo.api import (
    ErrorSolicitud,
    OrdenEstable,
    PaginacionAjustable,
    entero_de_query,
    fecha_de_query,
)
from apps.nucleo.esquema import ErrorSerializer
from apps.recepcion import serializers
from apps.recepcion.models import Adjunto, Documento
from apps.recepcion.views.adjunto import descarga
from apps.seguridad.alcance import AlcanceEmisorMixin

RESPUESTA_XML = {(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer}
RESPUESTA_PDF = {(200, "application/pdf"): OpenApiTypes.BINARY, 400: ErrorSerializer}


class DocumentoRecibidoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Facturas y notas que los proveedores les mandaron a los emisores.

    De solo lectura: las crea el procesamiento de los correos de recepción.

    Filtros: ``?emisor=<id>``, ``?correo=<id>``,
    ``?documento_tipo=factura_venta|nota_credito|nota_debito``,
    ``?proveedor=<NIT sin DV>`` y ``?desde=AAAA-MM-DD`` / ``?hasta=AAAA-MM-DD``
    sobre la fecha de emisión, ambos inclusive. ``?ordering=`` ordena por
    ``fecha_emision``, ``numero``, ``total_a_pagar`` o ``creado_en``.

    ``?search=`` busca en cuatro campos, cada uno a su manera:

    - CUFE/CUDE: completo y exacto. Uno parcial no tiene sentido.
    - NIT del proveedor: por el comienzo (``8001`` encuentra ``800111222``).
    - Número y razón social: en cualquier parte.

    Es el listado de más consumo de la recepción, así que pagina de 25 en 25
    por defecto, con ``?page_size=`` hasta 100, y siempre desempata el orden
    por ``id`` para que ninguna fila se repita ni se pierda entre páginas.

    Los archivos se bajan con ``xml/`` (el XML tal como llegó), ``xml-factura/``
    (el documento, sin el AttachedDocument) y ``pdf/``. Son ``Adjunto`` del
    correo; también salen en ``/api/recepcion/adjunto/?documento=<uuid>``.
    """

    serializer_class = serializers.DocumentoRecibidoSerializer
    queryset = Documento.objects.select_related("documento_tipo", "moneda")
    pagination_class = PaginacionAjustable

    filter_backends = [filters.SearchFilter, OrdenEstable]
    # Los prefijos de DRF: `=` es igualdad (iexact) y `^` es "empieza por".
    # Los índices de `Documento.Meta` están hechos para estas búsquedas.
    search_fields = [
        "=cufe_cude", "^proveedor_numero_identificacion", "numero", "proveedor_razon_social",
    ]
    ordering_fields = ["fecha_emision", "numero", "total_a_pagar", "creado_en"]

    def get_queryset(self):
        """Los filtros acotan dentro del alcance, nunca lo amplían: el mixin ya
        restringió el queryset antes de llegar aquí."""
        qs = super().get_queryset().annotate(
            # Dos subconsultas EXISTS en la misma consulta del listado, en vez
            # de traerse todas las filas de adjuntos de la página solo para
            # saber si hay PDF y XML del documento.
            tiene_pdf=_tiene_adjunto(Adjunto.Rol.PDF),
            tiene_xml_factura=_tiene_adjunto(Adjunto.Rol.XML_DOCUMENTO),
        )
        params = self.request.query_params
        if (emisor := entero_de_query(params, "emisor")) is not None:
            qs = qs.filter(emisor=emisor)
        if (correo := entero_de_query(params, "correo")) is not None:
            qs = qs.filter(correo=correo)
        if tipo := params.get("documento_tipo"):
            qs = qs.filter(documento_tipo__codigo=tipo)
        if proveedor := params.get("proveedor"):
            qs = qs.filter(proveedor_numero_identificacion=proveedor)
        if desde := fecha_de_query(params, "desde"):
            qs = qs.filter(fecha_emision__gte=desde)
        if hasta := fecha_de_query(params, "hasta"):
            qs = qs.filter(fecha_emision__lte=hasta)
        return qs

    @extend_schema(responses=RESPUESTA_XML)
    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        """El XML tal como llegó: el AttachedDocument o el documento suelto."""
        return descarga(self._adjunto(Adjunto.Rol.XML))

    @extend_schema(responses=RESPUESTA_XML)
    @action(detail=True, methods=["get"], url_path="xml-factura")
    def xml_factura(self, request, pk=None):
        """El documento electrónico, sin el AttachedDocument que lo envolvía.

        Si llegó suelto, es el mismo XML de ``xml/``.
        """
        documento = self.get_object()
        adjunto = _de(documento, Adjunto.Rol.XML_DOCUMENTO) or _de(documento, Adjunto.Rol.XML)
        if adjunto is None:
            raise ErrorSolicitud("El documento no tiene XML.")
        return descarga(adjunto)

    @extend_schema(responses=RESPUESTA_PDF)
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """La representación gráfica que mandó el proveedor."""
        return descarga(self._adjunto(
            Adjunto.Rol.PDF, "El proveedor no mandó el PDF de este documento.",
        ))

    def _adjunto(self, rol, mensaje="El documento no tiene XML."):
        adjunto = _de(self.get_object(), rol)
        if adjunto is None:
            raise ErrorSolicitud(mensaje)
        return adjunto


def _de(documento, rol):
    return documento.adjuntos.filter(rol=rol).first()


def _tiene_adjunto(rol):
    return Exists(Adjunto.objects.filter(documento=OuterRef("pk"), rol=rol))
