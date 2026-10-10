"""API de los documentos recibidos de proveedores."""
import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from django.db.models import Exists, OuterRef
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.nucleo.api import (
    ErrorSolicitud,
    OrdenEstable,
    PaginacionAjustable,
    entero_de_query,
    fecha_de_query,
)
from apps.nucleo.esquema import ErrorSerializer
from apps.nucleo.registro import campos
from apps.recepcion import adjuntos, eventos, procesamiento, serializers, verificacion
from apps.recepcion.models import Adjunto, Documento
from apps.recepcion.views.adjunto import descarga
from apps.seguridad.alcance import AlcanceEmisorMixin, usuario_del_request

logger = logging.getLogger(__name__)

RESPUESTA_XML = {(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer}
RESPUESTA_PDF = {(200, "application/pdf"): OpenApiTypes.BINARY, 400: ErrorSerializer}


class DocumentoRecibidoViewSet(
    AlcanceEmisorMixin, mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet,
):
    """Facturas y notas que los proveedores les mandaron a los emisores.

    Las crea el procesamiento de los correos de recepción (o la carga manual).
    ``DELETE`` elimina uno **sin eventos RADIAN**, con sus archivos.

    Filtros: ``?emisor=<id>``, ``?correo=<id>``,
    ``?documento_tipo=factura_venta|nota_credito|nota_debito``,
    ``?proveedor=<NIT sin DV>``, ``?verificacion_estado=valido|invalido|...``,
    ``?radian_estado=sin_eventos|acuse|recibo|aceptada|reclamada``
    y ``?desde=AAAA-MM-DD`` / ``?hasta=AAAA-MM-DD`` sobre la fecha de
    emisión, ambos inclusive. ``?ordering=`` ordena por
    ``fecha_emision``, ``numero``, ``total_a_pagar`` o ``creado_en``.

    ``?search=`` busca en cuatro campos, cada uno a su manera:

    - CUFE/CUDE: completo y exacto. Uno parcial no tiene sentido.
    - NIT del proveedor: por el comienzo (``8001`` encuentra ``800111222``).
    - Número y razón social: en cualquier parte.

    Es el listado de más consumo de la recepción, así que pagina de 25 en 25
    por defecto, con ``?page_size=`` hasta 100, y siempre desempata el orden
    por ``id`` para que ninguna fila se repita ni se pierda entre páginas.

    ``POST cargar/`` registra los documentos de un ZIP o XML subido a mano,
    para quien no quiere recibirlos por correo.

    Cada documento nuevo se verifica solo contra la DIAN (``GetStatus`` por su
    CUFE, en segundo plano): ``verificacion_estado`` dice si la DIAN lo tiene
    como válido. ``POST {id}/verificar/`` repite la consulta en el momento.

    ``POST {id}/evento/`` pide un evento RADIAN sobre la factura (030, 031,
    032 o 033); ``radian_estado`` resume el último que la DIAN registró, y el
    detalle está en ``/api/recepcion/evento/?documento=<uuid>``.

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

    @extend_schema(responses={204: None, 400: ErrorSerializer, 502: ErrorSerializer})
    def destroy(self, request, *args, **kwargs):
        """Elimina el documento y sus archivos en B2, si no tiene eventos.

        Con cualquier evento, en cualquier estado, responde 400: uno registrado
        está en RADIAN y uno rechazado guarda la respuesta de la DIAN. El
        correo se queda con su MIME y sus demás adjuntos; si era una carga
        manual y este era su único documento, se elimina también.

        Si B2 falla, las filas vuelven y responde 502; repetir termina el
        trabajo.
        """
        documento = self.get_object()
        try:
            carga_borrada, archivos = adjuntos.eliminar_documento(documento)
        except adjuntos.DocumentoConEventos as error:
            raise ErrorSolicitud(str(error))
        logger.info("recepcion.documento_eliminado %s", campos(
            documento=kwargs["pk"], numero=documento.numero, emisor=documento.emisor_id,
            correo=documento.correo_id, carga_borrada=carga_borrada, archivos=archivos,
            por=request.user,
        ))
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_serializer_class(self):
        if self.action == "evento":
            return serializers.SolicitudEventoSerializer
        return super().get_serializer_class()

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
        if estado := params.get("verificacion_estado"):
            qs = qs.filter(verificacion_estado=estado)
        if radian := params.get("radian_estado"):
            qs = qs.filter(radian_estado=radian)
        return qs

    @extend_schema(
        request={"multipart/form-data": serializers.CargaDocumentoSerializer},
        responses={
            201: serializers.ResultadoCargaSerializer,
            200: serializers.ResultadoCargaSerializer,
            400: ErrorSerializer,
            502: ErrorSerializer,
        },
    )
    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser])
    def cargar(self, request):
        """Registra los documentos de un ZIP o XML, sin pasar por el correo.

        ``multipart/form-data`` con ``emisor`` y ``archivo`` (``.zip`` o
        ``.xml``, hasta 10 MB). Se procesa en el momento, con la misma lectura
        que los correos: el ZIP del proveedor con su AttachedDocument y su
        PDF, o el XML suelto.

        Cada documento tiene que ser **del emisor elegido** (su NIT receptor):
        los de otro van en ``rechazados``. Un CUFE ya registrado va en
        ``repetidos`` y no se duplica.

        - 201 si creó al menos uno. La carga queda en
          ``/api/recepcion/correo/?origen=carga``, con sus archivos.
        - 200 si todos estaban ya registrados. No se guarda nada.
        - 400 si el archivo no trae documentos o todos son de otro receptor.
        """
        entrada = serializers.CargaDocumentoSerializer(
            data=request.data, context=self.get_serializer_context(),
        )
        entrada.is_valid(raise_exception=True)
        archivo = entrada.validated_data["archivo"]
        try:
            resultado = procesamiento.cargar(
                entrada.validated_data["emisor"], usuario_del_request(request),
                archivo.name, archivo.read(),
            )
        except procesamiento.CargaInvalida as error:
            raise ErrorSolicitud(str(error))
        respuesta = serializers.ResultadoCargaSerializer({
            "creados": resultado.creados,
            "repetidos": [d for d in resultado.repetidos if d is not None],
            "rechazados": [
                {
                    "numero": r.datos.numero,
                    "cufe_cude": r.datos.cufe_cude,
                    "receptor_numero_identificacion": r.datos.receptor_numero_identificacion,
                    "motivo": r.motivo,
                }
                for r in resultado.rechazados
            ],
        })
        codigo = status.HTTP_201_CREATED if resultado.creados else status.HTTP_200_OK
        return Response(respuesta.data, status=codigo)

    @extend_schema(request=None, responses={200: serializers.DocumentoRecibidoSerializer})
    @action(detail=True, methods=["post"])
    def verificar(self, request, pk=None):
        """Consulta el CUFE en la DIAN ahora mismo y devuelve el documento.

        Para repetir una verificación que quedó en ``error`` o
        ``no_verificable`` (por ejemplo, después de cargar el certificado), o
        para confirmar un estado. Si la DIAN no responde, el documento queda
        en ``error`` con el detalle; la respuesta es 200 igual, porque lo que
        se pidió —consultar— se intentó.
        """
        documento = self.get_object()
        try:
            verificacion.verificar(documento)
        except verificacion.ErrorTransitorio as error:
            verificacion.marcar_error(documento, str(error))
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(
        request=serializers.SolicitudEventoSerializer,
        responses={201: serializers.EventoSerializer, 400: ErrorSerializer},
    )
    @action(detail=True, methods=["post"])
    def evento(self, request, pk=None):
        """Pide un evento RADIAN sobre la factura y lo envía a la DIAN.

        ``{"codigo": "030"|"031"|"032"|"033"}``, con ``persona`` en el 030 y el
        032 (si no viene, la configurada en el emisor) y ``concepto_reclamo``
        en el 031. Responde 201 con el evento ``pendiente``: se envía en segundo
        plano y su estado se consulta en ``/api/recepcion/evento/{id}/``.

        400 si no cabe: no es una factura de venta, la DIAN no la tiene
        verificada como válida, ya tiene ese evento, falta el anterior
        (030 → 032 → 033 o 031), el 033 y el 031 se excluyen, o venció el
        plazo de 3 días hábiles desde el 032.
        """
        documento = self.get_object()
        entrada = self.get_serializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        datos = entrada.validated_data
        try:
            evento = eventos.solicitar(
                documento, datos["codigo"], usuario=usuario_del_request(request),
                persona=datos.get("persona"), concepto_reclamo=datos.get("concepto_reclamo"),
            )
        except eventos.EventoInvalido as error:
            raise ErrorSolicitud(str(error))
        return Response(
            serializers.EventoSerializer(evento, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

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
