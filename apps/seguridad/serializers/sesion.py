"""Serializers de la sesión y del segundo factor."""
from django.contrib.auth import get_user_model
from rest_framework import serializers

Usuario = get_user_model()


class UsuarioMeSerializer(serializers.ModelSerializer):
    """Lo que el front necesita saber de quien acaba de entrar.

    Deliberadamente corto: esto viaja en cada ingreso y en cada `me/`, y lo demás
    se pide cuando haga falta.
    """

    mfa_activo = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = [
            "id", "email", "nombre_corto", "is_staff", "is_verified",
            "mfa_activo",
        ]
        read_only_fields = fields

    def get_mfa_activo(self, usuario) -> bool:
        mfa = getattr(usuario, "mfa", None)
        return bool(mfa and mfa.activo)


class IngresoSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={"input_type": "password"})


class MfaIngresoSerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    codigo = serializers.CharField()
    recordar_dispositivo = serializers.BooleanField(default=False)


class ReenvioMfaSerializer(serializers.Serializer):
    mfa_token = serializers.CharField()


class MfaEnrolarSerializer(serializers.Serializer):
    """Elige el método. El secreto TOTP lo genera el servidor, no el cliente."""

    from apps.seguridad.models import METODOS

    metodo = serializers.ChoiceField(choices=METODOS)


class MfaConfirmarSerializer(serializers.Serializer):
    codigo = serializers.CharField()


class MfaDesactivarSerializer(serializers.Serializer):
    """Apagar el segundo factor exige la contraseña.

    Quien se siente frente a una sesión abierta ajena no puede quitarlo sin
    saberla: sería la forma más fácil de desarmar la cuenta.
    """

    password = serializers.CharField(style={"input_type": "password"})


class RecuperacionSerializer(serializers.Serializer):
    """Pide el enlace para restablecer la contraseña."""

    email = serializers.EmailField()


class RestablecerSerializer(serializers.Serializer):
    """Fija la contraseña nueva con el token del enlace."""

    token = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password

        validate_password(value)
        return value
