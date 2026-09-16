"""API de documentos electrónicos y acciones del ciclo de vida DIAN."""
import requests
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import FileResponse, HttpResponse
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.dian import representacion, servicios
from apps.dian.errores import error_pasarela_dian
from apps.documentos import serializers
from apps.documentos.models import Documento, DocumentoEstado
from apps.documentos.servicios import (
    ErrorNotificacion,
    empaquetar_notificacion,
    enviar_notificacion,
    nombre_dian,
)
from apps.nucleo.api import (
    ErrorPasarela,
    ErrorSolicitud,
    cuerpo_de_error,
    entero_de_query,
)
from apps.nucleo.esquema import ErrorSerializer
from apps.seguridad.alcance import AlcanceEmisorMixin
from apps.utilidades.zinc import ZincNoDisponible

CODIGO_DOCUMENTO_DUPLICADO = "documento_duplicado"


def mensaje_documento_duplicado(documento):
    """Mensaje del 409: la ruta no va aquí sino en la cabecera `Location`."""
    return (
        f"El documento {documento.numero} ya fue creado; su ruta va en la "
        "cabecera Location."
    )


class DocumentoViewSet(
    AlcanceEmisorMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Un documento no se edita: se crea, se emite y, si estaba mal, se borra.

    **Sin `PUT` ni `PATCH`** sobre el documento. No es una restricción de
    permisos sino de forma: un documento fiscal es un hecho con fecha, número y
    firma, y editarlo en sitio abre la puerta a que lo que se emitió y lo que se
    guarda dejen de coincidir. Mientras es borrador, corregirlo es borrarlo y
    volver a crearlo —que además libera el consecutivo—; una vez emitido, lo que
    corrige una factura es una nota, no un `PATCH`.

    Lo que sí cambia el documento son las acciones de más abajo, cada una con su
    regla: `emitir` (firma y envía), `actualizar-estado`, `notificar`. El estado no es
    un campo que se escriba, es la consecuencia de una operación.
    """


    queryset = (
        Documento.objects.select_related(
            "documento_tipo", "estado", "emisor", "adquiriente", "resolucion",
            "moneda",
            # Solo lo tiene el P.O.S., pero es 1:1: traerlo aquí evita una
            # consulta por documento al serializarlo y no cuesta nada en los
            # tipos que no lo llevan.
            "pos",
        ).prefetch_related("errores", "adquiriente__responsabilidades")
    )

    # `?ordering=fecha_emision,hora_emision` (el `-` invierte cada campo). Sin
    # el parámetro manda el orden del modelo: lo más reciente primero. La lista
    # es explícita para no exponer al ordenamiento columnas sin índice ni rutas
    # que arrastren joins.
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    # `?search=` no hacía nada: el backend estaba puesto pero sin campos, y DRF
    # devuelve el queryset entero cuando `search_fields` está vacío. Se busca
    # por lo que un humano tiene delante al preguntar «¿esta factura salió?»:
    # el número, el identificador que la DIAN devuelve, y el NIT o el nombre
    # del receptor. La lista es corta y explícita para no arrastrar joins en
    # cada búsqueda; es el mismo criterio que `ordering_fields`.
    search_fields = [
        "numero", "cufe_cude",
        "adquiriente__numero_identificacion", "adquiriente__razon_social",
    ]
    ordering_fields = [
        "fecha_emision", "hora_emision", "consecutivo", "numero",
        "total_a_pagar", "fecha_validacion", "creado_en", "actualizado_en",
        "estado__nombre", "documento_tipo__codigo", "notificado",
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return serializers.DocumentoCrearSerializer
        if self.action == "list":
            return serializers.DocumentoListaSerializer
        return serializers.DocumentoSerializer

    @extend_schema(
        responses={201: serializers.DocumentoCrearSerializer, 409: ErrorSerializer},
        parameters=[
            OpenApiParameter(
                name="Location", type=str, location=OpenApiParameter.HEADER,
                response=[409],
                description=(
                    "Ruta del documento que ya existe con el mismo emisor, "
                    "tipo, prefijo y consecutivo."
                ),
            ),
        ],
    )
    def create(self, request, *args, **kwargs):
        """Crea el documento, o responde 409 si ese número ya existe.

        El orden es **estructura → duplicado → datos**. La estructura va
        primero, como en toda la recepción. El duplicado va antes que los datos
        porque es la respuesta que necesita un reintento: el ERP que perdió la
        respuesta del primer intento y repite la petición al día siguiente ya
        no pasaría la fecha de emisión, y un 400 por la fecha le escondería que
        el documento existe.

        El 409 lleva el cuerpo de error común (código `documento_duplicado`) y
        la ruta del existente en `Location`: es donde HTTP la pone, y así el
        cuerpo no se sale de `detail` + `errores`.

        La búsqueda previa no cubre la carrera —dos peticiones iguales que la
        pasan a la vez—: esa la resuelve la restricción de unicidad de la base,
        y el `IntegrityError` se traduce al mismo 409.
        """
        serializer = self.get_serializer(data=request.data)
        errores = serializer.errores_de_estructura(request.data)
        if errores:
            raise ValidationError(errores)

        existente = self._existente(request.data)
        if existente is not None:
            return self._respuesta_duplicado(existente)

        serializer.is_valid(raise_exception=True)
        try:
            # Punto de guardado propio: tras el `IntegrityError` la transacción
            # queda inservible, y hace falta consultar quién ganó la carrera.
            with transaction.atomic():
                self.perform_create(serializer)
        except IntegrityError:
            existente = self._existente(request.data)
            if existente is None:
                # No era el duplicado: no se tapa otro error de integridad.
                raise
            return self._respuesta_duplicado(existente)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

    def _existente(self, datos):
        """El documento con la misma identidad dentro del alcance, o ``None``.

        Se busca con los datos crudos porque va antes de validarlos: si alguno
        no tiene forma de id o de número, no hay nada que buscar y lo dirá la
        validación. Dentro del alcance, para que un documento de un emisor
        ajeno no se revele: ahí se responde como a cualquier emisor ajeno.
        """
        try:
            identidad = {
                "emisor_id": int(datos["emisor"]),
                "documento_tipo_id": int(datos["documento_tipo"]),
                "prefijo": str(datos["prefijo"]),
                "consecutivo": int(datos["consecutivo"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
        return self.get_queryset().filter(**identidad).first()

    def _respuesta_duplicado(self, documento):
        return Response(
            cuerpo_de_error(
                mensaje_documento_duplicado(documento), CODIGO_DOCUMENTO_DUPLICADO,
            ),
            status=status.HTTP_409_CONFLICT,
            headers={"Location": reverse("documento-detail", args=[documento.pk])},
        )

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
        # De los errores, en la lista solo va el conteo: se anota con un COUNT y
        # se deja de traer las filas, que son las que engordan la respuesta.
        if self.action == "list":
            # `prefetch_related(None)` limpia los del queryset base; se repone el
            # del adquiriente, que la lista sí serializa con sus responsabilidades.
            qs = (
                qs.prefetch_related(None)
                .prefetch_related("adquiriente__responsabilidades")
                .annotate(total_errores=Count("errores"))
                # `annotate` con un agregado **descarta** el `Meta.ordering` del
                # modelo —Django lo hace para no meter esos campos en el GROUP
                # BY—, así que sin esto el listado paginado sale en el orden que
                # quiera PostgreSQL: una fila puede repetirse en dos páginas y
                # otra no aparecer en ninguna. Se repone explícitamente.
                .order_by(*Documento._meta.ordering)
            )
        else:
            qs = qs.prefetch_related("detalles__impuestos")
        params = self.request.query_params
        if (emisor := entero_de_query(params, "emisor")) is not None:
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

    def _bloquear(self, obj):
        """Relee el objeto con ``FOR UPDATE``. Hay que estar en transacción.

        Sin esto, dos peticiones simultáneas sobre el mismo documento hacen el
        trabajo dos veces: dos `emitir` gastan dos veces el consecutivo y dejan
        dos XML firmados con CUFE distinto, y dos envíos mandan el mismo
        documento dos veces a la DIAN, que responde el segundo con "procesado
        anteriormente" —o algo peor, si el primero aún no había terminado—.

        El bloqueo es de fila, así que solo espera quien toque **ese mismo**
        documento. Sí mantiene abierta la transacción mientras dura la llamada
        SOAP, que es el precio de que el envío sea de uno en uno: es justo la
        garantía que se busca.
        """
        return type(obj).objects.select_for_update().get(pk=obj.pk)

    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Firma el documento y lo envía a la DIAN, en una sola llamada.

        Antes eran dos acciones, `emitir` (firmar) y `enviar`; nadie firmaba
        sin enviar a continuación, y la segunda llamada solo sumaba un viaje.

        **Son dos transacciones, y a propósito.** La firma se confirma antes de
        enviar: si el envío falla por red —o la DIAN recibe el documento y la
        respuesta se pierde—, el documento queda `firmado` con su CUFE, y el
        reintento manda **ese mismo** CUFE. En una sola transacción la firma se
        desharía con el fallo, el reintento firmaría con otra hora y otro CUFE
        para el mismo número, y la DIAN, que ya tenía el primero, lo rechazaría.
        Por eso un 502 aquí no pierde nada: se vuelve a llamar.

        Según el estado: `borrador` se firma y se envía; `firmado` —un intento
        anterior que no llegó a enviar— solo se envía. `enviado`, `aceptado` y
        `rechazado` responden 400: el primero se sigue con
        `actualizar-estado/`, y un rechazado no se reemite, se borra y se crea
        corregido.
        """
        documento = self.get_object()
        try:
            with transaction.atomic():
                documento = self._bloquear(documento)
                if documento.estado.nombre != DocumentoEstado.Nombre.FIRMADO:
                    # Lo que no se puede firmar lo explica el propio servicio.
                    servicios.generar_y_firmar(documento)
            with transaction.atomic():
                documento = self._bloquear(documento)
                # Entre las dos transacciones otra petición pudo enviarlo.
                if documento.estado.nombre != DocumentoEstado.Nombre.FIRMADO:
                    raise servicios.ErrorEmision(
                        f"El documento {documento.numero} ya no está firmado y "
                        f"pendiente de envío: está '{documento.estado.nombre}'."
                    )
                respuesta = servicios.enviar_a_dian(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,
            "cufe_cude": documento.cufe_cude,
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
        —el documento firmado y el acuse de la DIAN juntos—.

        Con ``?descargar=1`` **no envía**: devuelve el zip para revisarlo. Es la
        forma de ver qué se le va a mandar al cliente sin mandárselo.
        """
        documento = self.get_object()
        entrada = serializers.NotificacionSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        pdf = entrada.validated_data.get("pdf")
        adjuntos = entrada.validated_data.get("adjuntos") or []

        if request.query_params.get("descargar"):
            try:
                paquete = empaquetar_notificacion(documento, pdf=pdf, adjuntos=adjuntos)
            except ErrorNotificacion as exc:
                raise ErrorSolicitud(str(exc))
            respuesta = HttpResponse(paquete.contenido, content_type=paquete.tipo)
            respuesta["Content-Disposition"] = f'attachment; filename="{paquete.nombre}"'
            return respuesta

        try:
            paquete, respuesta_zinc = enviar_notificacion(
                documento, pdf=pdf, adjuntos=adjuntos,
            )
        except ErrorNotificacion as exc:
            raise ErrorSolicitud(str(exc))
        except ZincNoDisponible as exc:
            # 502: el documento está bien, quien falló fue la pasarela de correo.
            # El documento NO queda marcado como notificado, así que se reintenta.
            raise ErrorPasarela(str(exc))

        return Response({
            "destinatario": paquete.destinatario,
            "archivo": paquete.nombre,
            "tipo": paquete.tipo,
            "tamano": paquete.tamano,
            "contenido": paquete.archivos,
            "notificado": documento.notificado,
            "enviado": True,
            "codigo_envio": respuesta_zinc.get("codigoEnvio", ""),
            "respuesta": respuesta_zinc,
        })

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Descarga la representación gráfica (PDF) del documento."""
        documento = self.get_object()
        if not documento.cufe_cude:
            raise ErrorSolicitud("El documento debe emitirse antes de generar el PDF.")
        contenido = representacion.generar_pdf(documento)
        respuesta = HttpResponse(contenido, content_type="application/pdf")
        respuesta["Content-Disposition"] = f'inline; filename="{documento.numero}.pdf"'
        return respuesta
