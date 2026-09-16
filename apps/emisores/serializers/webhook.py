"""Serializer de los webhooks del emisor."""
from rest_framework import serializers

from apps.emisores.models import Emisor, Webhook
from apps.emisores.models.webhook import validar_url_https
from apps.seguridad.alcance import RelacionDelAlcance

MENSAJE_EMISOR_INMUTABLE = (
    "Un webhook no cambia de emisor: bórrelo y créelo en el otro."
)


class WebhookSerializer(serializers.ModelSerializer):
    # Solo dentro del alcance: un emisor ajeno responde igual que uno que no
    # existe, para no revelar qué ids hay en otras cuentas.
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    # `CharField` y no el `URLField` de DRF: ese trae su propio validador de URL
    # y una dirección mal formada saldría con dos errores —"URL no válida" y
    # "tiene que ser HTTPS"— en vez de uno. El del modelo cubre los dos casos.
    url = serializers.CharField(max_length=500, validators=[validar_url_https])

    class Meta:
        model = Webhook
        fields = [
            "id", "emisor", "nombre", "url",
            "estado_validado", "estado_notificado",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = ["creado_en", "actualizado_en"]

    def validate_emisor(self, emisor):
        if self.instance is not None and emisor != self.instance.emisor:
            raise serializers.ValidationError(MENSAJE_EMISOR_INMUTABLE)
        return emisor
