"""Alta pública del usuario."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    validate_password as validar_password_django,
)
from rest_framework import serializers

Usuario = get_user_model()


class RegistroSerializer(serializers.Serializer):
    """Crea el usuario, y solo el usuario.

    Solo el usuario: no hay nada más que crear. Los emisores se dan de alta
    después, ya autenticado, y quedan a su nombre. El alta hace una sola cosa
    —demostrar que el correo existe— y no obliga a inventar un nombre de empresa
    antes de haber entrado.

    Es un ``Serializer`` suelto y no un ``ModelSerializer``, a propósito: al
    declarar los campos uno a uno, un cuerpo con ``is_staff`` o
    ``is_verified`` no tiene por dónde entrar. Con un ``ModelSerializer``
    esa protección dependería de acertar con ``fields``/``read_only_fields``, y
    aquí un descuido cuesta la plataforma entera.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )
    nombre_corto = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_email(self, value):
        """Normaliza el correo y rechaza el que ya esté dado de alta.

        Decir que el correo ya existe revela que hay una cuenta con esa
        dirección. Se acepta ese coste: el correo es único en la base, así que
        un mensaje genérico no ocultaría nada —el alta fallaría igual— y sí
        dejaría a quien se registra sin saber que ya tiene cuenta.
        """
        email = Usuario.objects.normalize_email(value)
        if Usuario.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Ya hay una cuenta con este correo. Inicia sesión o recupera la "
                "contraseña."
            )
        return email

    def validate_password(self, value):
        validar_password_django(value)
        return value

    def create(self, validated_data):
        return Usuario.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            nombre_corto=validated_data.get("nombre_corto") or None,
        )


class ReenvioSerializer(serializers.Serializer):
    """Pide otra vez el correo de verificación."""

    email = serializers.EmailField()


class VerificacionSerializer(serializers.Serializer):
    """Confirma el correo con el token que viajó en el enlace."""

    token = serializers.CharField()
