"""Serializer de los webhooks del emisor o de la cuenta."""
from rest_framework import serializers

from apps.emisores.models import Cuenta, Emisor, Webhook
from apps.emisores.models.webhook import validar_url_web
from apps.seguridad.alcance import RelacionDelAlcance, es_staff

MENSAJE_EMISOR_INMUTABLE = (
    "Un webhook no cambia de emisor: bórrelo y créelo en el otro."
)
MENSAJE_CUENTA_INMUTABLE = (
    "Un webhook no cambia de cuenta: bórrelo y créelo en la otra."
)
MENSAJE_EMISOR_O_CUENTA = (
    "Indique el 'emisor' o la 'cuenta' del webhook: uno de los dos, no ambos."
)
MENSAJE_CUENTA_SOLO_STAFF = (
    "Solo el staff de la plataforma registra webhooks de una cuenta."
)


class WebhookSerializer(serializers.ModelSerializer):
    # Solo dentro del alcance: un emisor ajeno responde igual que uno que no
    # existe, para no revelar qué ids hay en otras cuentas.
    emisor = RelacionDelAlcance(
        queryset=Emisor.objects.all(), campo_emisor="id",
        required=False, allow_null=True,
    )
    # En vez de `emisor`: el webhook vale para todos los emisores de la cuenta
    # y se suma a los que cada uno tenga. Solo staff, como las propias cuentas.
    cuenta = serializers.PrimaryKeyRelatedField(
        queryset=Cuenta.objects.all(), required=False, allow_null=True,
    )
    # `CharField` y no el `URLField` de DRF: ese trae su propio validador de URL
    # y una dirección mal formada saldría con dos errores —"URL no válida" y
    # "tiene que ser http o https"— en vez de uno. El del modelo cubre los dos.
    url = serializers.CharField(max_length=500, validators=[validar_url_web])
    # Solo de escritura mientras no se decida cómo mostrarlo: no sale en
    # ninguna respuesta. El tope es del secreto en claro; cifrado ocupa más, y
    # por eso la columna es de 512. Vacío lo quita.
    secreto = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255,
    )

    class Meta:
        model = Webhook
        fields = [
            "id", "emisor", "cuenta", "nombre", "url",
            "estado_validado", "estado_notificado", "secreto",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = ["creado_en", "actualizado_en"]

    def validate_emisor(self, emisor):
        if self.instance is not None and emisor != self.instance.emisor:
            raise serializers.ValidationError(MENSAJE_EMISOR_INMUTABLE)
        return emisor

    def validate_cuenta(self, cuenta):
        if self.instance is not None and cuenta != self.instance.cuenta:
            raise serializers.ValidationError(MENSAJE_CUENTA_INMUTABLE)
        return cuenta

    def validate(self, attrs):
        # El dueño no cambia después de crear (lo cuidan los dos de arriba), así
        # que solo ahí hay que mirar que venga uno y nada más que uno.
        if self.instance is None:
            emisor, cuenta = attrs.get("emisor"), attrs.get("cuenta")
            if (emisor is None) == (cuenta is None):
                raise serializers.ValidationError(MENSAJE_EMISOR_O_CUENTA)
            request = self.context.get("request")
            # Un webhook de cuenta recibe los avisos de todos sus emisores:
            # quien solo alcanza a uno no puede ponerlo.
            if cuenta is not None and request is not None and not es_staff(request):
                raise serializers.ValidationError(
                    {"cuenta": MENSAJE_CUENTA_SOLO_STAFF}
                )
        return attrs
