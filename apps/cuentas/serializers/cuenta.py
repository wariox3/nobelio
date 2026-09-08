"""Serializer de la cuenta (cliente/tenant)."""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.cuentas.models import Cuenta


class CuentaSerializer(serializers.ModelSerializer):
    """La cuenta y su dueño.

    ``usuario`` no es obligatorio en el cuerpo: para quien no es staff lo pone
    la vista con el del solicitante, y mandarlo no sirve de nada. Solo el staff,
    que da de alta cuentas ajenas, tiene que indicarlo.
    """

    usuario = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False
    )

    class Meta:
        model = Cuenta
        fields = [
            "id", "nombre", "usuario", "identificacion", "correo_contacto",
            "activa", "creado_en",
        ]
        read_only_fields = ["id", "creado_en"]
