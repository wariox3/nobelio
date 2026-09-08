"""Login JWT que además exige el correo confirmado."""
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class TokenVerificadoSerializer(TokenObtainPairSerializer):
    """Entrega el par de tokens solo si el correo está verificado.

    SimpleJWT solo mira `is_active`, así que la regla hay que ponerla aquí. Se
    podría haber usado `USER_AUTHENTICATION_RULE`, que es global, pero entonces
    el rechazo saldría con el mensaje genérico de credenciales inválidas: quien
    acaba de registrarse leería "usuario o contraseña incorrectos" cuando lo que
    pasa es que no ha abierto el correo. Se comprueba después de autenticar, así
    que el mensaje solo lo ve quien ya demostró saber la contraseña.
    """

    def validate(self, attrs):
        datos = super().validate(attrs)
        if not self.user.is_verified:
            raise serializers.ValidationError(
                "Tienes que confirmar tu correo antes de iniciar sesión. "
                "Si no te llegó, pide otro desde "
                "/api/seguridad/registro/reenviar/."
            )
        return datos
