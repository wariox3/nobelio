"""API de los webhooks del emisor."""
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as campos
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.emisores.servicios import webhooks
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.nucleo.esquema import ErrorSerializer
from apps.seguridad.alcance import AlcanceEmisorMixin

RESPUESTA_PRUEBA = inline_serializer(
    name="WebhookPruebaRespuesta",
    fields={
        "entregado": campos.BooleanField(),
        "codigo_http": campos.IntegerField(allow_null=True),
        "detalle": campos.CharField(),
        "duracion_ms": campos.IntegerField(),
    },
)


class WebhookViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """Los webhooks del emisor: a qué URL avisarle, y de qué.

    Un emisor puede tener varios. `estado_validado` y `estado_notificado` dicen
    si a ese webhook se le avisa de la validación de la DIAN y de la
    notificación al adquiriente. El aviso sale firmado con el `secreto` del
    webhook, una sola vez y sin reintentos; lo que respondió cada uno está en
    ``/api/emisores/webhook-aviso/``. Sin secreto, o si el emisor no tiene
    `referencia_externa`, no se manda: el aviso queda fallido con el motivo.

    Filtro: ``?emisor=<id>``.
    """

    serializer_class = serializers.WebhookSerializer
    queryset = models.Webhook.objects.select_related("emisor")
    # Declarado para que `probar` lo pueda fijar en su `@action`: el router
    # solo acepta ahí atributos que la clase ya tenga. En el resto no aplica.
    throttle_scope = None

    def get_queryset(self):
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        return qs.filter(emisor=emisor) if emisor else qs

    @extend_schema(request=None, responses={200: RESPUESTA_PRUEBA, 400: ErrorSerializer})
    # Con tope propio: cada llamada hace que nobelio le pegue a una URL que
    # puso el cliente y le devuelva lo que respondió.
    @action(detail=True, methods=["post"], throttle_scope="webhook_prueba")
    def probar(self, request, pk=None):
        """Manda un aviso de prueba al webhook y devuelve lo que respondió.

        ``POST /api/emisores/webhook/{id}/probar/``. Va a la misma URL y con la
        misma firma que los avisos reales, con ``{"tipo": "prueba", "cliente":
        <referencia_externa>}``. El receptor comprueba la firma y que el
        cliente exista, y responde sin tocar nada.

        Responde **200** con el resultado aunque el receptor falle: `entregado`
        es true solo si contestó 200. Lo habitual: 401 es secreto distinto o
        reloj desfasado; 404, que el receptor no conoce la
        `referencia_externa`; `codigo_http` nulo, que la URL no respondió (el
        motivo va en `detalle`). Es **400** si el webhook no tiene secreto o el
        emisor no tiene `referencia_externa`: entonces no se manda nada.

        No deja registro en ``/api/emisores/webhook-aviso/``.
        """
        webhook = self.get_object()
        try:
            resultado = webhooks.probar(webhook)
        except webhooks.PruebaImposible as exc:
            raise ErrorSolicitud(str(exc))
        return Response(resultado)
