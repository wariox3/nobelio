"""Serializer del usuario para la API."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    validate_password as validar_password_django,
)
from rest_framework import serializers

Usuario = get_user_model()


class UsuarioSerializer(serializers.ModelSerializer):
    """Lectura y escritura de usuarios. La contraseña es de solo escritura."""

    password = serializers.CharField(
        write_only=True, required=False, style={"input_type": "password"}
    )

    class Meta:
        model = Usuario
        fields = [
            "id", "email", "nombres", "apellidos",
            "is_staff", "is_active", "is_superuser",
            "password", "creado_en",
        ]
        read_only_fields = ["id", "is_superuser", "creado_en"]

    def validate_password(self, value):
        validar_password_django(value)
        return value

    def validate(self, attrs):
        # La contraseña es obligatoria al crear, opcional al actualizar.
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "Es obligatoria al crear un usuario."}
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return Usuario.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
