"""API de nóminas electrónicas y acciones del ciclo de vida DIAN."""
import requests
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import filters, mixins, viewsets
from rest_framework import serializers as campos
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import servicios
from apps.dian.errores import error_pasarela_dian
from apps.dian.esquema import (
    RESPUESTA_CONSULTA_DIAN,
    campo_track_id,
    campos_respuesta_dian,
)
from apps.documentos.models import DocumentoEstado
from apps.nomina import serializers
from apps.nomina.models import Nomina
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.nucleo.esquema import ErrorSerializer
from apps.seguridad.alcance import AlcanceEmisorMixin

# Las acciones no reciben la nómina ni la devuelven, pero spectacular las
# describía con el serializer del ViewSet: `emitir/` pedía una nómina entera como
# cuerpo y prometía devolver otra. Aquí se declara lo que de verdad entra y sale;
# lo que comparte con documentos está en `apps.dian.esquema`. El 401, el 429 y el
# 404 los añade `apps.nucleo.esquema.documentar_errores`; el 400 y el 502 de las
# acciones sin cuerpo los declaran ellas.
RESPUESTA_EMISION_NOMINA = inline_serializer(
    name="EmisionNominaRespuesta",
    fields={
        **campos_respuesta_dian(),
        "cune": campos.CharField(help_text="CUNE de la nómina firmada."),
        "track_id": campo_track_id(),
    },
)


class NominaViewSet(
    AlcanceEmisorMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Nómina electrónica y su nota de ajuste.

    Comparte el ciclo de vida de los documentos electrónicos —borrador, firmado,
    enviado, aceptado o rechazado— pero no su pipeline: la nómina no es UBL y va
    por ``SendNominaSync``.

    **Sin `PUT` ni `PATCH`**, por lo mismo que en `DocumentoViewSet`: un
    documento fiscal es un hecho con fecha, número y firma, y editarlo en sitio
    abre la puerta a que lo que se emitió y lo que se guarda dejen de coincidir.
    Mientras es borrador, corregirlo es borrarlo y volver a crearlo; una vez
    emitido, lo que corrige una nómina es su **nota de ajuste** (`tipo_xml`
    103), que la DIAN tiene prevista justamente para esto.

    Lo que cambia el documento son las acciones, cada una con su regla. El
    estado no es un campo que se escriba.
    """

    queryset = Nomina.objects.select_related(
        "emisor", "empleado", "estado", "periodo_nomina", "moneda",
        "nomina_predecesora",
    ).prefetch_related("errores")

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["numero", "cune", "empleado__numero_documento"]
    ordering_fields = [
        "fecha_generacion", "consecutivo", "numero", "total_comprobante",
        "fecha_validacion", "creado_en", "actualizado_en", "estado__nombre",
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return serializers.NominaCrearSerializer
        if self.action == "list":
            return serializers.NominaListaSerializer
        return serializers.NominaSerializer

    def get_queryset(self):
        """Filtra por ``emisor``, ``empleado``, ``estado`` (nombre) y ``tipo_xml``.

        P. ej. ``?emisor=4&estado=aceptado`` o ``?tipo_xml=103`` para las notas
        de ajuste.
        """
        qs = super().get_queryset()
        # Igual que en documentos: en la lista los errores van contados, así que
        # se anotan y no se traen sus filas.
        if self.action == "list":
            # El `annotate` descarta el `Meta.ordering` del modelo (Django lo
            # hace para no meter esos campos en el GROUP BY), y sin orden la
            # paginación puede repetir una fila y saltarse otra. Se repone.
            qs = (
                qs.prefetch_related(None)
                .annotate(total_errores=Count("errores"))
                .order_by(*Nomina._meta.ordering)
            )
        else:
            qs = qs.prefetch_related("conceptos")
        params = self.request.query_params
        if (emisor := entero_de_query(params, "emisor")) is not None:
            qs = qs.filter(emisor=emisor)
        if (empleado := entero_de_query(params, "empleado")) is not None:
            qs = qs.filter(empleado=empleado)
        if estado := params.get("estado"):
            qs = qs.filter(estado__nombre=estado)
        if tipo := params.get("tipo_xml"):
            qs = qs.filter(tipo_xml=tipo)
        return qs

    def destroy(self, request, *args, **kwargs):
        """Solo se borra lo que no está aceptado por la DIAN.

        Una nómina aceptada ya existe para la DIAN: se corrige con una nota de
        ajuste, no borrándola de aquí.
        """
        nomina = self.get_object()
        if nomina.estado_id and nomina.estado.nombre == DocumentoEstado.Nombre.ACEPTADO:
            raise ErrorSolicitud(
                "La nómina fue aceptada por la DIAN: corríjala con una nota de "
                "ajuste en vez de borrarla."
            )
        return super().destroy(request, *args, **kwargs)

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

    @extend_schema(
        request=None,
        responses={200: RESPUESTA_EMISION_NOMINA, 400: ErrorSerializer, 502: ErrorSerializer},
    )
    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Firma la nómina y la envía a la DIAN, en una sola llamada.

        Antes eran dos acciones, `emitir` (firmar) y `enviar`, igual que en
        documentos; nadie firmaba sin enviar a continuación, y la segunda
        llamada solo sumaba un viaje.

        El envío va al Set de Pruebas (``SendTestSetAsync``) mientras el emisor
        esté en habilitación de nómina, y por ``SendNominaSync`` después; lo
        decide el servicio, no el llamador.

        **Son dos transacciones, y a propósito.** La firma se confirma antes de
        enviar: si el envío falla por red —o la DIAN recibe la nómina y la
        respuesta se pierde—, la nómina queda `firmado` con su CUNE, y el
        reintento manda **ese mismo** CUNE. En una sola transacción la firma se
        desharía con el fallo y el reintento firmaría con otra `HoraGen` y otro
        CUNE para el mismo número. Por eso un 502 aquí no pierde nada: se
        vuelve a llamar.

        Según el estado: `borrador` se firma y se envía; `firmado` —un intento
        anterior que no llegó a enviar— solo se envía. `enviado`, `aceptado` y
        `rechazado` responden 400: el primero se sigue con `consultar/`, un
        aceptado se corrige con una nota de ajuste, y un rechazado —la nómina no
        se edita— se borra y se crea de nuevo corregido.
        """
        nomina = self.get_object()
        try:
            with transaction.atomic():
                nomina = self._bloquear(nomina)
                if nomina.estado.nombre != DocumentoEstado.Nombre.FIRMADO:
                    # Lo que no se puede firmar lo explica el propio servicio.
                    servicios.generar_y_firmar_nomina(nomina)
            with transaction.atomic():
                nomina = self._bloquear(nomina)
                # Entre las dos transacciones otra petición pudo enviarla.
                if nomina.estado.nombre != DocumentoEstado.Nombre.FIRMADO:
                    raise servicios.ErrorEmision(
                        f"La nómina {nomina.numero} ya no está firmada y "
                        f"pendiente de envío: está '{nomina.estado.nombre}'."
                    )
                respuesta = servicios.enviar_nomina_a_dian(nomina)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": nomina.estado.nombre,
            "cune": nomina.cune,
            "track_id": respuesta.track_id,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @extend_schema(
        request=None,
        responses={200: RESPUESTA_CONSULTA_DIAN, 400: ErrorSerializer, 502: ErrorSerializer},
    )
    @action(detail=True, methods=["get", "post"])
    def consultar(self, request, pk=None):
        """Consulta el estado en la DIAN y lo aplica a la nómina.

        Pregunta por el ZipKey si la nómina salió al Set de Pruebas y por el
        CUNE si salió por la operación síncrona: son dos consultas distintas y
        la entrega asíncrona no se puede consultar por CUNE.

        Y **aplica** lo que responda: guarda la respuesta cruda, deja los
        rechazos en ``NominaError`` y mueve el estado. Hace falta porque el
        envío al Set de Pruebas no trae veredicto —es asíncrono y solo devuelve
        el ZipKey—, así que sin esto una nómina rechazada se queda en
        ``enviado`` y sin errores, y encima bloqueada para volver a emitirse.

        Desde ``aceptado`` o sin enviar se limita a leer: el primero es terminal
        y el segundo no tiene nada que consultar.

        Acepta GET y POST. El GET escribe, que no es lo ortodoxo, pero es lo que
        ya llamaba el ERP y tener dos acciones para esto resultó ser una fuente
        de confusión más que una ayuda.
        """
        nomina = self.get_object()
        try:
            if servicios.estado_actualizable(nomina):
                respuesta = servicios.actualizar_estado_nomina(nomina)
            else:
                respuesta = servicios.consultar_segun_envio_nomina(nomina)
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)
        return Response({
            "estado": nomina.estado.nombre,
            "es_valido": respuesta.es_valido,
            "codigo_estado": respuesta.codigo_estado,
            "descripcion": respuesta.descripcion_estado,
            "errores": respuesta.errores,
        })

    @extend_schema(
        responses={(200, "application/xml"): OpenApiTypes.BINARY, 400: ErrorSerializer},
    )
    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        """Descarga el XML firmado."""
        nomina = self.get_object()
        if not nomina.xml_archivo:
            raise ErrorSolicitud("La nómina no está firmada; emítala primero.")
        respuesta = HttpResponse(nomina.leer_xml(), content_type="application/xml")
        respuesta["Content-Disposition"] = (
            f'attachment; filename="{nomina.numero}.xml"'
        )
        return respuesta
