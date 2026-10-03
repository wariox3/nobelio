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
            "id", "usuario", "nombre", "prefijo",
            "activa", "expira_en", "alcance_global", "ultimo_uso_en",
            "creado_en", "clave",
        ]
        # El dueño lo pone la vista con quien hace la petición: una llave a
        # nombre de otro sería una credencial para suplantarlo.
        # `alcance_global` solo se enciende por CLI: por la API, una credencial
        # podría ascenderse a sí misma o a otra.
        read_only_fields = [
            "id", "usuario", "prefijo", "alcance_global", "ultimo_uso_en",
            "creado_en", "clave",
        ]

    def validate_expira_en(self, valor):
        """Una llave de alcance global no puede quedarse sin vencimiento ni
        alargarlo más allá del tope."""
        llave = self.instance
        if llave is not None and llave.alcance_global:
            if valor is None:
                raise serializers.ValidationError(
                    "Una llave de alcance global tiene que tener vencimiento."
                )
            if problema := llave.problema_alcance_global(expira_en=valor):
                raise serializers.ValidationError(problema)
        return valor

    def get_clave(self, obj) -> str | None:
        # Solo está presente justo después de crear la llave (ver create()).
        return getattr(obj, "_clave_completa", None)

    def create(self, validated_data):
        llave, clave_completa = LlaveApi.generar(
            usuario=validated_data["usuario"],
            nombre=validated_data["nombre"],
            activa=validated_data.get("activa", True),
            expira_en=validated_data.get("expira_en"),
        )
        llave._clave_completa = clave_completa
        return llave
