"""API de documentos electrónicos y acciones del ciclo de vida DIAN."""
import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import FileResponse, HttpResponse
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import filters, mixins, status, viewsets
from rest_framework import serializers as campos
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.dian import representacion, servicios
from apps.dian.errores import error_pasarela_dian
from apps.dian.esquema import (
    RESPUESTA_CONSULTA_DIAN,
    campos_emision,
    campos_respuesta_dian,
)
from apps.documentos import serializers
from apps.documentos.tareas import emitir_documento
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
from apps.emisores.serializers import WebhookAvisoSerializer
from apps.emisores.servicios import webhooks
from apps.nucleo.colas import encolar
from apps.nucleo.esquema import ErrorSerializer
from apps.seguridad.alcance import AlcanceEmisorMixin
from apps.utilidades.zinc import ZincNoDisponible

CODIGO_DOCUMENTO_DUPLICADO = "documento_duplicado"

# --- Esquema de las acciones --------------------------------------------------
# Las acciones no reciben el documento ni lo devuelven, pero spectacular las
# describía con el serializer del ViewSet: `emitir/` pedía un documento entero
# como cuerpo y prometía devolver otro. Quien generara un cliente desde
# `schema.yml` mandaba un cuerpo que nadie lee y esperaba una forma que no llega.
# Aquí se declara lo que de verdad entra y sale.
#
# El 401, el 429 y el 404 los añade `apps.nucleo.esquema.documentar_errores` a
# todas las rutas de detalle; el 400 solo donde hay cuerpo, así que las acciones
# sin cuerpo que responden 400 lo declaran ellas, igual que el 502.
#
# Lo que comparte con nómina —las consultas a la DIAN— está en `apps.dian.esquema`.

RESPUESTA_EMISION = inline_serializer(
    name="EmisionRespuesta",
    fields={
        **campos_respuesta_dian(),
        **campos_emision(),
        "cufe_cude": campos.CharField(help_text="CUFE o CUDE del documento firmado."),
    },
)
RESPUESTA_NOTIFICACION = inline_serializer(
    name="NotificacionRespuesta",
    fields={
        "destinatario": campos.EmailField(help_text="Correo del adquiriente."),
        "archivo": campos.CharField(help_text="Nombre del zip enviado."),
        "tipo": campos.CharField(help_text="Tipo MIME del paquete: `application/zip`."),
        "tamano": campos.IntegerField(help_text="Tamaño del zip, en bytes."),
        "contenido": campos.ListField(
            child=campos.CharField(), help_text="Nombres de los archivos dentro del zip.",
        ),
        "notificado": campos.BooleanField(),
        "enviado": campos.BooleanField(),
        "codigo_envio": campos.CharField(help_text="Identificador del envío en la pasarela de correo."),
        "respuesta": campos.DictField(help_text="Respuesta cruda de la pasarela de correo."),
    },
)
RESPUESTA_VALIDADO = inline_serializer(
    name="RespuestaValidadoRespuesta",
    fields={
        "respuesta_validado": campos.BooleanField(),
        "avisos": WebhookAvisoSerializer(many=True),
    },
)

MENSAJE_RESPUESTA_NO_ACEPTADO = (
    "Solo se avisa la validación de un documento aceptado por la DIAN; este "
    "está '{estado}'."
)
MENSAJE_RESPUESTA_YA_VALIDADO = "La validación de este documento ya fue respondida."
MENSAJE_RESPUESTA_SIN_200 = (
    "Ningún webhook respondió 200 ({resultados}). El documento sigue sin "
    "respuesta de validado; el detalle de cada aviso está en "
    "/api/emisores/webhook-aviso/?documento={documento}."
)


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
    regla: `emitir` (firma, envía o consulta), `notificar`. El estado no es
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
        "respuesta_validado",
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

        El 201 sale en `borrador`, y al confirmarse se encola su emisión: un
        worker de Celery lo firma y lo envía a la DIAN (`emitir_documento`). El
        resultado se lee en el documento, o llega por el webhook de validación.
        La creación no espera a la DIAN ni depende del broker: si no responde,
        el documento se queda en `borrador` y se emite con `emitir/`.

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
        if settings.DOCUMENTOS_EMITIR_AL_CREAR:
            # El 201 sale en `borrador`; la firma y el envío van en el worker.
            # Si el broker no responde, se queda así y se emite con `emitir/`.
            encolar(emitir_documento, str(serializer.instance.pk))
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
        ``documento_tipo`` (ambos por código), ``notificado`` y
        ``respuesta_validado`` (true/false): p. ej.
        ``?emisor=2&estado=aceptado&documento_tipo=factura_venta``, o
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
        for bandera in ("notificado", "respuesta_validado"):
            if (valor := params.get(bandera)) is not None:
                qs = qs.filter(**{bandera: valor.lower() in ("1", "true", "si", "sí")})
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

    @extend_schema(
        request=None,
        responses={200: RESPUESTA_EMISION, 400: ErrorSerializer, 502: ErrorSerializer},
    )
    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Lleva el documento a su estado final ante la DIAN.

        Antes eran dos acciones, `emitir` (firmar) y `enviar`; nadie firmaba
        sin enviar a continuación, y la segunda llamada solo sumaba un viaje.

        **Son dos transacciones, y a propósito.** La firma se confirma antes de
        enviar: si el envío falla por red —o la DIAN recibe el documento y la
        respuesta se pierde—, el documento queda `firmado` con su CUFE, y el
        reintento manda **ese mismo** CUFE. En una sola transacción la firma se
        desharía con el fallo, el reintento firmaría con otra hora y otro CUFE
        para el mismo número, y la DIAN, que ya tenía el primero, lo rechazaría.
        Por eso un 502 aquí no pierde nada: se vuelve a llamar.

        Según el estado:

        - `borrador`: se firma y se envía.
        - `firmado` —un intento anterior que no llegó a enviar—: solo se envía.
        - `enviado` —se envió sin veredicto, como en el Set de Pruebas—: **se
          consulta y se aplica** el resultado, sin reenviar. Por eso no hay
          `actualizar-estado/`: el ERP llama a `emitir/` hasta que el estado sea
          final.
        - `aceptado` y `rechazado` responden 400. El rechazado no se reemite: su
          detalle se lee con `consultar/`, y se borra y se crea corregido.

        La consulta va con el mismo bloqueo que el envío, para que dos llamadas
        a la vez no apliquen el resultado dos veces.
        """
        documento = self.get_object()
        try:
            documento, accion, respuesta = servicios.emitir(documento)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": documento.estado.nombre,
            "accion": accion,
            "cufe_cude": documento.cufe_cude,
            "track_id": documento.track_id,
            "fecha_validacion": documento.fecha_validacion,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @extend_schema(
        responses={200: RESPUESTA_CONSULTA_DIAN, 400: ErrorSerializer, 502: ErrorSerializer},
    )
    @action(detail=True, methods=["get"])
    def consultar(self, request, pk=None):
        """Consulta (solo lectura) el estado del **documento** en la DIAN.

        ``GET /api/documentos/documento/{id}/consultar/`` → GetStatus, por el
        CUFE. Es la pregunta "¿cómo quedó este documento?", y la respuesta vale
        igual para un envío síncrono que para uno del Set de Pruebas.

        No modifica el documento, en ningún estado —también sirve para leer el
        detalle de un rechazado—; devuelve lo que responde la DIAN. Lo aplica
        `emitir/`, cuando el documento quedó enviado sin veredicto.
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

    @extend_schema(
        request=None,
        responses={200: RESPUESTA_VALIDADO, 400: ErrorSerializer, 502: ErrorSerializer},
    )
    @action(detail=True, methods=["post"], url_path="respuesta-validado")
    def respuesta_validado(self, request, pk=None):
        """Avisa la validación a los webhooks del emisor y, con un 200, la da por respondida.

        ``POST /api/documentos/documento/{id}/respuesta-validado/``. El documento
        tiene que estar aceptado y sin `respuesta_validado`. Se manda el aviso de
        validación a cada webhook del emisor con `estado_validado`, esperando la
        respuesta, y si alguno contesta **200** el documento queda con
        `respuesta_validado = true`.

        Si el emisor no tiene ninguno, no hay a quién avisar: el documento se
        marca igual, sin enviar nada, y `avisos` sale vacío.

        Es lo mismo que se hace solo cuando la DIAN acepta el documento; este
        endpoint recupera los que allí se quedaron sin marcar. Si ninguno
        responde 200 es un 502: el documento sigue igual y se puede volver a
        llamar. Cada envío deja su aviso en
        ``/api/emisores/webhook-aviso/``, con el código y el error.
        """
        documento = self.get_object()
        estado = documento.estado.nombre if documento.estado_id else ""
        if estado != DocumentoEstado.Nombre.ACEPTADO:
            raise ErrorSolicitud(MENSAJE_RESPUESTA_NO_ACEPTADO.format(estado=estado))
        if documento.respuesta_validado:
            raise ErrorSolicitud(MENSAJE_RESPUESTA_YA_VALIDADO)

        # La misma función que corre sola cuando la DIAN acepta el documento:
        # esto es para recuperar los que allí se quedaron sin marcar.
        respondido, avisos = webhooks.responder_validado(documento)
        if not respondido:
            resultados = ", ".join(
                f"{aviso.webhook.nombre}: {aviso.codigo_http or aviso.error}"
                for aviso in avisos
            )
            raise ErrorPasarela(MENSAJE_RESPUESTA_SIN_200.format(
                resultados=resultados, documento=documento.pk,
            ))

        return Response({
            "respuesta_validado": True,
            "avisos": WebhookAvisoSerializer(avisos, many=True).data,
        })

    @extend_schema(
        responses={(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer},
    )
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

    @extend_schema(
        responses={(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer},
    )
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

    @extend_schema(
        request={"multipart/form-data": serializers.NotificacionSerializer},
        parameters=[
            OpenApiParameter(
                name="descargar", type=OpenApiTypes.BOOL, location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Con `1` no envía nada: devuelve el zip (`application/zip`) "
                    "para revisarlo."
                ),
            ),
        ],
        responses={
            (200, "application/json"): RESPUESTA_NOTIFICACION,
            (200, "application/zip"): OpenApiTypes.BINARY,
            400: ErrorSerializer,
            502: ErrorSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def notificar(self, request, pk=None):
        """Arma lo que se le entrega al adquiriente y lo deja listo para enviar.

        ``POST /api/documentos/documento/{id}/notificar/`` en multipart, con
        ``correo``, ``pdf`` y ``adjuntos`` opcionales (hasta 10 MB entre los
        archivos). El resultado es siempre un zip, y dentro va siempre el
        AttachedDocument —el documento firmado y el acuse de la DIAN juntos—.

        Si viene ``correo``, reemplaza el del adquiriente del documento y el
        envío va ahí; si no, va al que ya tenía. El cambio se guarda aunque la
        pasarela falle, para que el reintento lo use.

        Con ``?descargar=1`` **no envía** ni guarda el correo: devuelve el zip
        para revisarlo. Es la forma de ver qué se le va a mandar al cliente sin
        mandárselo.

        Cada envío que llega a la pasarela, salga o falle, queda registrado en
        ``/api/documentos/documento-notificacion/``; la descarga no.
        """
        documento = self.get_object()
        entrada = serializers.NotificacionSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        pdf = entrada.validated_data.get("pdf")
        adjuntos = entrada.validated_data.get("adjuntos") or []
        correo = entrada.validated_data.get("correo")

        if request.query_params.get("descargar"):
            try:
                paquete = empaquetar_notificacion(
                    documento, pdf=pdf, adjuntos=adjuntos, correo=correo,
                )
            except ErrorNotificacion as exc:
                raise ErrorSolicitud(str(exc))
            respuesta = HttpResponse(paquete.contenido, content_type=paquete.tipo)
            respuesta["Content-Disposition"] = f'attachment; filename="{paquete.nombre}"'
            return respuesta

        try:
            paquete, respuesta_zinc = enviar_notificacion(
                documento, pdf=pdf, adjuntos=adjuntos, correo=correo,
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

    @extend_schema(
        responses={(200, "application/pdf"): OpenApiTypes.BINARY, 400: ErrorSerializer},
    )
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
