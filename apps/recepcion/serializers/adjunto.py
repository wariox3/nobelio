"""Serializer de los adjuntos de los correos."""
from rest_framework import serializers

from apps.recepcion.models import Adjunto


class AdjuntoSerializer(serializers.ModelSerializer):
    """Un archivo del correo. El contenido no va aquí: se baja con ``descargar/``."""

    class Meta:
        model = Adjunto
        fields = [
            "id", "correo", "documento", "rol", "nombre", "tipo_contenido",
            "tamano", "sha256", "creado_en",
        ]
        read_only_fields = fields
