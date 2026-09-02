"""API de nóminas electrónicas y acciones del ciclo de vida DIAN."""
import requests
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import servicios
from apps.documentos.models import DocumentoEstado
from apps.nomina import serializers
from apps.nomina.models import Nomina
from apps.nomina.servicios import crear_nota_ajuste
from apps.nucleo.api import ErrorSolicitud, entero_de_query, error_pasarela_dian
from apps.seguridad.alcance import AlcanceEmisorMixin


class NominaViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """Nómina electrónica y su nota de ajuste.

    Comparte el ciclo de vida de los documentos electrónicos —borrador, firmado,
    enviado, aceptado o rechazado— pero no su pipeline: la nómina no es UBL y va
    por ``SendNominaSync``.
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
        if self.action in ("create", "update", "partial_update"):
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

    @action(detail=True, methods=["post"], url_path="nota-ajuste")
    def nota_ajuste(self, request, pk=None):
        """Crea la nota de ajuste de esta nómina y la deja en borrador.

        ``POST /api/nomina/nomina/{id}/nota-ajuste/`` con
        ``{"tipo_nota": "1"}`` para reemplazar el documento anterior o
        ``{"tipo_nota": "2"}`` para eliminarlo, y opcionalmente ``prefijo``,
        ``consecutivo`` y ``notas``.

        La nota se **clona** del documento que ajusta porque el reemplazo lo
        repite entero (numeral 5.5.8): no lleva diferencias sino la nómina
        corregida completa. Reenviarla campo a campo por ``POST /nomina/`` sigue
        estando permitido, pero es donde se cuela un dato que ya no coincide con
        el original.

        Sale idéntica al documento anterior: lo que haya que corregir se edita
        en el borrador —``PATCH``— y luego van ``emitir`` y ``enviar``.
        """
        nomina = self.get_object()
        try:
            nota = crear_nota_ajuste(
                nomina,
                tipo_nota=str(request.data.get("tipo_nota") or ""),
                prefijo=request.data.get("prefijo"),
                consecutivo=request.data.get("consecutivo") or None,
                notas=request.data.get("notas"),
            )
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))
        except IntegrityError:
            # El consecutivo se pide bajo bloqueo, así que esto solo salta si
            # el cliente manda uno a mano que ya existe. Sin capturarlo era un
            # 500 con un mensaje de PostgreSQL.
            raise ErrorSolicitud(
                "Ya existe una nómina con ese prefijo y consecutivo para el "
                "emisor. Use otro número o deje que se asigne solo."
            )
        return Response(
            serializers.NominaSerializer(nota).data,
            status=status.HTTP_201_CREATED,
        )

    def _bloquear(self, obj):
        """Relee el objeto con ``FOR UPDATE``. Hay que estar en transacción.

        Sin esto, dos peticiones simultáneas sobre el mismo documento hacen el
        trabajo dos veces: dos `emitir` gastan dos veces el consecutivo y dejan
        dos XML firmados con CUFE distinto, y dos `enviar` mandan el mismo
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
        """Genera el XML, calcula el CUNE y firma la nómina."""
        nomina = self.get_object()
        try:
            with transaction.atomic():
                nomina = self._bloquear(nomina)
                servicios.generar_y_firmar_nomina(nomina)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": nomina.estado.nombre,
            "cune": nomina.cune,
        })

    @action(detail=True, methods=["post"])
    def enviar(self, request, pk=None):
        """Envía la nómina firmada a la DIAN.

        Va al Set de Pruebas (``SendTestSetAsync``) mientras el emisor esté en
        habilitación de nómina, y por ``SendNominaSync`` después; lo decide el
        servicio, no el llamador.
        """
        nomina = self.get_object()
        try:
            with transaction.atomic():
                nomina = self._bloquear(nomina)
                respuesta = servicios.enviar_nomina_a_dian(nomina)
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
