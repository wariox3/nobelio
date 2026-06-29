"""API de documentos electrónicos y acciones del ciclo de vida DIAN."""
from django.conf import settings
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import representacion, servicios
from apps.documentos import serializers
from apps.documentos.models import Documento
from apps.nucleo.api import ErrorSolicitud


class DocumentoViewSet(viewsets.ModelViewSet):
    """CRUD de documentos electrónicos y acciones del ciclo de vida DIAN."""

    queryset = (
        Documento.objects.select_related(
            "emisor", "adquiriente", "resolucion", "moneda"
        ).prefetch_related("detalles__impuestos")
    )

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return serializers.DocumentoCrearSerializer
        return serializers.DocumentoSerializer

    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Genera el XML UBL, calcula el CUFE y firma el documento."""
        documento = self.get_object()
        try:
            servicios.generar_y_firmar(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": documento.estado,
            "cufe_cude": documento.cufe_cude,
        })

    @action(detail=True, methods=["post"])
    def enviar(self, request, pk=None):
        """Envía el documento firmado a la DIAN (Set de Pruebas en habilitación)."""
        documento = self.get_object()
        try:
            respuesta = servicios.enviar_a_dian(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": documento.estado,
            "track_id": respuesta.track_id,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["post"])
    def consultar(self, request, pk=None):
        """Consulta en la DIAN el estado del documento por su track_id.

        En habilitación usa GetStatusZip (Set de Pruebas); en producción GetStatus.
        """
        documento = self.get_object()
        try:
            respuesta = servicios.consultar_estado(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": documento.estado,
            "track_id": documento.track_id,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        """Descarga el XML firmado del documento."""
        documento = self.get_object()
        if not documento.xml_firmado:
            raise ErrorSolicitud("El documento aún no está firmado.")
        respuesta = HttpResponse(documento.xml_firmado, content_type="application/xml")
        respuesta["Content-Disposition"] = f'attachment; filename="{documento.numero}.xml"'
        return respuesta

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Descarga la representación gráfica (PDF) del documento."""
        documento = self.get_object()
        if not documento.cufe_cude:
            raise ErrorSolicitud("El documento debe emitirse antes de generar el PDF.")
        contenido = representacion.generar_pdf(documento, ambiente=settings.DIAN_ENVIRONMENT)
        respuesta = HttpResponse(contenido, content_type="application/pdf")
        respuesta["Content-Disposition"] = f'inline; filename="{documento.numero}.pdf"'
        return respuesta
