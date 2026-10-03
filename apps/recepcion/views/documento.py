"""API de los documentos recibidos de proveedores."""
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action

from apps.nucleo.api import ErrorSolicitud, entero_de_query, fecha_de_query
from apps.nucleo.esquema import ErrorSerializer
from apps.recepcion import serializers
from apps.recepcion.models import Documento
from apps.seguridad.alcance import AlcanceEmisorMixin

RESPUESTA_XML = {(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer}
RESPUESTA_PDF = {(200, "application/pdf"): OpenApiTypes.BINARY, 400: ErrorSerializer}


class DocumentoRecibidoViewSet(AlcanceEmisorMixin, viewsets.ReadOnlyModelViewSet):
    """Facturas y notas que los proveedores les mandaron a los emisores.

    De solo lectura: las crea el procesamiento de los correos de recepción.

    Filtros: ``?emisor=<id>``, ``?correo=<id>``,
    ``?documento_tipo=factura_venta|nota_credito|nota_debito``,
    ``?proveedor=<NIT sin DV>`` y ``?desde=AAAA-MM-DD`` / ``?hasta=AAAA-MM-DD``
    sobre la fecha de emisión, ambos inclusive. ``?search=`` busca en número,
    CUFE, NIT y razón social del proveedor, y ``?ordering=`` ordena por
    ``fecha_emision``, ``numero``, ``total_a_pagar`` o ``creado_en``.

    Los archivos se bajan con ``xml/`` (el XML tal como llegó), ``xml-factura/``
    (el documento, sin el AttachedDocument) y ``pdf/``.
    """

    serializer_class = serializers.DocumentoRecibidoSerializer
    queryset = Documento.objects.select_related("documento_tipo", "moneda")

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "numero", "cufe_cude", "proveedor_numero_identificacion", "proveedor_razon_social",
    ]
    ordering_fields = ["fecha_emision", "numero", "total_a_pagar", "creado_en"]

    def get_queryset(self):
        """Los filtros acotan dentro del alcance, nunca lo amplían: el mixin ya
        restringió el queryset antes de llegar aquí."""
        qs = super().get_queryset()
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
        documento = self.get_object()
        return _descarga(documento.xml_archivo, f"{documento.numero}.xml", "application/xml")

    @extend_schema(responses=RESPUESTA_XML)
    @action(detail=True, methods=["get"], url_path="xml-factura")
    def xml_factura(self, request, pk=None):
        """El documento electrónico, sin el AttachedDocument que lo envolvía.

        Si llegó suelto, es el mismo XML de ``xml/``.
        """
        documento = self.get_object()
        archivo = documento.xml_factura_archivo or documento.xml_archivo
        return _descarga(archivo, f"{documento.numero}-documento.xml", "application/xml")

    @extend_schema(responses=RESPUESTA_PDF)
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """La representación gráfica que mandó el proveedor."""
        documento = self.get_object()
        if not documento.pdf_archivo:
            raise ErrorSolicitud("El proveedor no mandó el PDF de este documento.")
        return _descarga(documento.pdf_archivo, f"{documento.numero}.pdf", "application/pdf")


def _descarga(archivo, nombre, tipo):
    """Stream desde el almacenamiento, sin cargar el archivo en memoria."""
    return FileResponse(
        archivo.open("rb"), content_type=tipo, as_attachment=True, filename=nombre,
    )
