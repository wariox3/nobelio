"""API de nóminas electrónicas y acciones del ciclo de vida DIAN."""
import requests
from django.db.models import Count
from django.http import HttpResponse
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.dian import servicios
from apps.documentos.models import DocumentoEstado
from apps.nomina import serializers
from apps.nomina.models import Nomina
from apps.nucleo.api import ErrorSolicitud, error_pasarela_dian
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
            qs = qs.prefetch_related(None).annotate(total_errores=Count("errores"))
        else:
            qs = qs.prefetch_related("conceptos")
        params = self.request.query_params
        if emisor := params.get("emisor"):
            qs = qs.filter(emisor=emisor)
        if empleado := params.get("empleado"):
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

    @action(detail=True, methods=["post"])
    def emitir(self, request, pk=None):
        """Genera el XML, calcula el CUNE y firma la nómina.

        Admite ``{"variante": "..."}`` mientras dure la investigación del
        rechazo ZE02: firma alineando con el documento que la DIAN aceptó el
        detalle que indique la variante. Ver ``apps.dian.variantes_firma``.
        """
        nomina = self.get_object()
        variante = request.data.get("variante")
        try:
            servicios.generar_y_firmar_nomina(nomina, variante=variante)
        except ValueError as exc:
            raise ErrorSolicitud(str(exc))
        except servicios.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        return Response({
            "estado": nomina.estado.nombre,
            "cune": nomina.cune,
            "variante": variante or "(ninguna)",
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
