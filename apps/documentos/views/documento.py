"""API de documentos electrónicos y acciones del ciclo de vida DIAN."""
import requests
from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import representacion, servicios
from apps.documentos import serializers
from apps.documentos.models import Documento, DocumentoEstado
from apps.documentos.servicios import (
    ErrorNotificacion,
    empaquetar_notificacion,
    marcar_notificado,
    nombre_dian,
)
from apps.nucleo.api import ErrorSolicitud, error_pasarela_dian
from apps.seguridad.alcance import AlcanceEmisorMixin


class DocumentoViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):

    queryset = (
        Documento.objects.select_related(
            "documento_tipo", "estado", "emisor", "adquiriente", "resolucion", "moneda"
        ).prefetch_related("errores", "adquiriente__responsabilidades")
    )

    # `?ordering=fecha_emision,hora_emision` (el `-` invierte cada campo). Sin
    # el parámetro manda el orden del modelo: lo más reciente primero. La lista
    # es explícita para no exponer al ordenamiento columnas sin índice ni rutas
    # que arrastren joins.
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    ordering_fields = [
        "fecha_emision", "hora_emision", "consecutivo", "numero",
        "total_a_pagar", "fecha_validacion", "creado_en", "actualizado_en",
        "estado__nombre", "documento_tipo__codigo", "notificado",
    ]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return serializers.DocumentoCrearSerializer
        if self.action == "list":
            return serializers.DocumentoListaSerializer
        return serializers.DocumentoSerializer

    def get_queryset(self):
        """Permite filtrar el listado por ``emisor`` (id), ``estado`` y
        ``documento_tipo`` (ambos por código) y ``notificado`` (true/false):
        p. ej. ``?emisor=2&estado=aceptado&documento_tipo=factura_venta``, o
        ``?estado=aceptado&notificado=false`` para lo que falta por entregar.

        El filtro por ``emisor`` acota dentro del alcance, nunca lo amplía: el
        mixin ya restringió el queryset antes de llegar aquí.
        """
        qs = super().get_queryset()
        # El listado no incluye las líneas; solo el detalle/retrieve las precarga.
        if self.action != "list":
            qs = qs.prefetch_related("detalles__impuestos")
        params = self.request.query_params
        if emisor := params.get("emisor"):
            qs = qs.filter(emisor=emisor)
        if estado := params.get("estado"):
            qs = qs.filter(estado__nombre=estado)
        if tipo := params.get("documento_tipo"):
            qs = qs.filter(documento_tipo__codigo=tipo)
        if (notificado := params.get("notificado")) is not None:
            qs = qs.filter(notificado=notificado.lower() in ("1", "true", "si", "sí"))
        return qs

    def destroy(self, request, *args, **kwargs):

        borrables = {
            DocumentoEstado.Nombre.BORRADOR,
            DocumentoEstado.Nombre.GENERADO,
            DocumentoEstado.Nombre.FIRMADO,
            DocumentoEstado.Nombre.ENVIADO,
            DocumentoEstado.Nombre.RECHAZADO,
        }
        documento = self.get_object()

        estado = documento.estado.nombre
        if estado not in borrables:
            raise ErrorSolicitud(
                f"No se puede borrar {documento.numero}: está en estado "
                f"'{estado}'. Solo se borran los documentos que la DIAN no ha "
                f"validado ({', '.join(sorted(borrables))})."
            )

        notas = list(documento.notas.values_list("numero", flat=True))
        if notas:
            raise ErrorSolicitud(
                f"No se puede borrar {documento.numero}: lo referencian las "
                f"notas {', '.join(notas)}. Bórrelas primero."
            )

        self.perform_destroy(documento)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Genera el XML UBL, calcula el CUFE y firma el documento."""
        documento = self.get_object()
        try:
            servicios.generar_y_firmar(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": documento.estado.nombre,
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
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,
            "track_id": respuesta.track_id,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["get"])
    def consultar(self, request, pk=None):
        """Consulta (solo lectura) el estado del **documento** en la DIAN.

        ``GET /api/documentos/documento/{id}/consultar/`` → GetStatus, por el
        CUFE. Es la pregunta "¿cómo quedó este documento?", y la respuesta vale
        igual para un envío síncrono que para uno del Set de Pruebas.

        No modifica el documento; devuelve lo que responde la DIAN. Para aplicar
        el resultado usa la acción ``actualizar-estado``.
        """
        documento = self.get_object()
        try:
            respuesta = servicios.consultar_estado(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,  # estado local (sin cambios)
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["get"], url_path="consultar-zip")
    def consultar_zip(self, request, pk=None):
        """Consulta (solo lectura) el **envío** al Set de Pruebas.

        ``GET /api/documentos/documento/{id}/consultar-zip/`` → GetStatusZip,
        por el ZipKey que devolvió ``SendTestSetAsync`` y que quedó en
        ``track_id``.

        Responde por esa entrega, no por el documento: si el mismo CUFE se
        envió más de una vez, sale como duplicado (regla 90) aunque el
        documento esté aceptado. Para saber cómo quedó el documento, la acción
        es ``consultar``.
        """
        documento = self.get_object()
        try:
            respuesta = servicios.consultar_estado_zip(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,  # estado local (sin cambios)
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["post"], url_path="actualizar-estado")
    def actualizar_estado(self, request, pk=None):
        """Consulta la DIAN y actualiza el estado del documento.

        Solo aplica a documentos enviados/rechazados (no aceptados ni en borrador).
        """
        documento = self.get_object()
        try:
            respuesta = servicios.actualizar_estado(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        """Descarga el XML firmado del documento (stream desde object storage)."""
        documento = self.get_object()
        if not documento.xml_archivo:
            raise ErrorSolicitud("El documento aún no está firmado.")
        respuesta = FileResponse(
            documento.xml_archivo.open("rb"),
            content_type="application/xml",
            as_attachment=True,
            filename=f"{documento.numero}.xml",
        )
        return respuesta

    @action(detail=True, methods=["get"])
    def attached(self, request, pk=None):
        """Descarga el AttachedDocument: el documento y el acuse de la DIAN juntos.

        ``GET /api/documentos/documento/{id}/attached/``. Es el paquete que se
        le entrega al adquiriente: dentro viajan el XML firmado y el
        ApplicationResponse con el que la DIAN acredita la validación.

        Se genera al vuelo, como el PDF: no aporta ningún dato que no esté ya en
        el XML del documento y en su respuesta guardada.
        """
        documento = self.get_object()
        try:
            contenido = servicios.generar_attached_document(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        respuesta = HttpResponse(contenido, content_type="application/xml")
        respuesta["Content-Disposition"] = (
            f'attachment; filename="{nombre_dian(documento, "ad")}.xml"'
        )
        return respuesta

    @action(detail=True, methods=["post"])
    def notificar(self, request, pk=None):
        """Arma lo que se le entrega al adquiriente y lo deja listo para enviar.

        ``POST /api/documentos/documento/{id}/notificar/`` en multipart, con
        ``pdf`` y ``adjuntos`` opcionales (hasta 10 MB entre todos). El
        resultado es siempre un zip, y dentro va siempre el AttachedDocument
        —el documento firmado y el acuse de la DIAN juntos—. Con
        ``?descargar=1`` devuelve el zip en vez del resumen, que es la forma de
        revisarlo mientras el correo no está implementado.

        El envío por correo todavía no existe: por eso la respuesta dice
        ``enviado: false``.
        """
        documento = self.get_object()
        entrada = serializers.NotificacionSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            paquete = empaquetar_notificacion(
                documento,
                pdf=entrada.validated_data.get("pdf"),
                adjuntos=entrada.validated_data.get("adjuntos") or [],
            )
        except ErrorNotificacion as exc:
            raise ErrorSolicitud(str(exc))

        marcar_notificado(documento)

        if request.query_params.get("descargar"):
            respuesta = HttpResponse(paquete.contenido, content_type=paquete.tipo)
            respuesta["Content-Disposition"] = f'attachment; filename="{paquete.nombre}"'
            return respuesta

        return Response({
            "destinatario": paquete.destinatario,
            "archivo": paquete.nombre,
            "tipo": paquete.tipo,
            "tamano": paquete.tamano,
            "contenido": paquete.archivos,
            "notificado": documento.notificado,
            "enviado": False,
            "detalle": "El paquete quedó armado; el envío por correo aún no está implementado.",
        })

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
