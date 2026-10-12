"""API de los webhooks del emisor o de la cuenta."""
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as campos
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.emisores.servicios import webhooks
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.nucleo.esquema import ErrorSerializer
from apps.seguridad.alcance import AlcanceEmisorMixin, es_staff

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
    """Los webhooks: a qué URL avisar de lo que pasa con los documentos, y de qué.

    Un webhook es de un `emisor` o de una `cuenta`, uno de los dos. El de la
    cuenta vale para todos sus emisores —también los que entren después— y se
    **suma** a los de cada emisor: un documento avisa a unos y a otros. Los de
    cuenta solo los ve y los administra el staff.

    Puede haber varios. `estado_validado` y `estado_notificado` dicen
    si a ese webhook se le avisa de la validación de la DIAN y de la
    notificación al adquiriente. El aviso sale firmado con el `secreto` del
    webhook, una sola vez y sin reintentos; lo que respondió cada uno está en
    ``/api/emisores/webhook-aviso/``. Sin secreto, o si el emisor no tiene
    `referencia_externa`, no se manda: el aviso queda fallido con el motivo.

    Filtros: ``?emisor=<id>``, ``?cuenta=<id>``.
    """

    serializer_class = serializers.WebhookSerializer
    queryset = models.Webhook.objects.select_related("emisor", "cuenta")
    # Declarado para que `probar` lo pueda fijar en su `@action`: el router
    # solo acepta ahí atributos que la clase ya tenga. En el resto no aplica.
    throttle_scope = None

    def get_queryset(self):
        qs = super().get_queryset()
        # Los de cuenta, solo staff, como las cuentas: el mixin ya se los quita
        # a quien tiene alcance por emisor, pero no a una llave global.
        if not es_staff(self.request):
            qs = qs.filter(cuenta__isnull=True)
        params = self.request.query_params
        if emisor := entero_de_query(params, "emisor"):
            qs = qs.filter(emisor=emisor)
        if cuenta := entero_de_query(params, "cuenta"):
            qs = qs.filter(cuenta=cuenta)
        return qs

    @extend_schema(
        request=inline_serializer(
            name="WebhookPruebaSolicitud",
            fields={"emisor": campos.IntegerField(required=False)},
        ),
        responses={200: RESPUESTA_PRUEBA, 400: ErrorSerializer},
    )
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

        El webhook de una cuenta no tiene un emisor propio: hay que indicar con
        cuál de los suyos probar, ``{"emisor": <id>}``. En el de un emisor ese
        campo no se usa.

        No deja registro en ``/api/emisores/webhook-aviso/``.
        """
        webhook = self.get_object()
        emisor = None
        if webhook.cuenta_id and request.data.get("emisor") not in (None, ""):
            try:
                emisor = models.Emisor.objects.filter(
                    pk=int(request.data["emisor"])
                ).first()
            except (TypeError, ValueError):
                raise ErrorSolicitud("'emisor' debe ser un número entero.")
            if emisor is None:
                raise ErrorSolicitud(webhooks.MENSAJE_PRUEBA_EMISOR_AJENO)
        try:
            resultado = webhooks.probar(webhook, emisor=emisor)
        except webhooks.PruebaImposible as exc:
            raise ErrorSolicitud(str(exc))
        return Response(resultado)
