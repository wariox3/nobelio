"""Serializer de las llaves de API (gestión desde el frontend, solo staff)."""
from rest_framework import serializers

from apps.seguridad.models import LlaveApi


class LlaveApiSerializer(serializers.ModelSerializer):
    """Gestiona llaves de API.

    El secreto (``clave``) solo se devuelve en la respuesta de creación; en
    lecturas posteriores es ``null``, porque no se almacena en claro.
    """

    clave = serializers.SerializerMethodField()

    class Meta:
        model = LlaveApi
        fields = [
            "id", "emisor", "nombre", "prefijo",
            "activa", "expira_en", "ultimo_uso_en", "creado_en", "clave",
        ]
        read_only_fields = ["id", "prefijo", "ultimo_uso_en", "creado_en", "clave"]

    def get_clave(self, obj):
        # Solo está presente justo después de crear la llave (ver create()).
        return getattr(obj, "_clave_completa", None)

    def create(self, validated_data):
        llave, clave_completa = LlaveApi.generar(
            emisor=validated_data["emisor"],
            nombre=validated_data["nombre"],
            activa=validated_data.get("activa", True),
            expira_en=validated_data.get("expira_en"),
        )
        llave._clave_completa = clave_completa
        return llave
