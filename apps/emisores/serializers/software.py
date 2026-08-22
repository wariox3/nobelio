"""Serializer del software DIAN del emisor."""
from rest_framework import serializers

from apps.emisores.models import SoftwareDian
from apps.emisores.servicios import motivo_no_puede_emitir


class SoftwareDianSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftwareDian
        fields = [
            "id", "emisor", "identificador", "pin",
            "test_set_id", "set_pruebas_aceptado", "activo",
        ]
        extra_kwargs = {
            # El PIN es sensible: se acepta al crear/editar pero nunca se devuelve.
            "pin": {"write_only": True},
        }

    def validate(self, attrs):
        """El emisor tiene que traer ya su certificado digital.

        El certificado va antes que el software en el flujo: es lo que firma la
        consulta de numeración y los documentos del Set de Pruebas, que son los
        dos pasos que siguen. Registrar el software sin él deja al emisor a
        medio habilitar, sin poder avanzar y sin que nada lo diga.
        """
        emisor = attrs.get("emisor") or getattr(self.instance, "emisor", None)
        if emisor is None:
            return attrs
        motivo = motivo_no_puede_emitir(emisor)
        if motivo:
            raise serializers.ValidationError({"emisor": motivo})
        return attrs