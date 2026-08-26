"""Serializer de la notificación al adquiriente: PDF y adjuntos opcionales."""
from rest_framework import serializers

from apps.documentos.servicios import TAMANO_MAXIMO_ADJUNTOS


def mensaje_adjuntos_muy_pesados(tamano):
    """Mensaje para un envío que se pasa del tope."""
    mb = TAMANO_MAXIMO_ADJUNTOS / (1024 * 1024)
    return (
        f"Los adjuntos suman {tamano / (1024 * 1024):.1f} MB y el máximo es "
        f"{mb:.0f} MB. Envíe menos archivos o más livianos."
    )


class NotificacionSerializer(serializers.Serializer):
    """Lo que el emisor sube para notificar. Todo opcional: sin nada, va el XML.

    El XML firmado no se recibe aquí —lo pone el sistema desde el storage—, así
    que el tope de 10 MB aplica solo a lo que llega en la petición.
    """

    pdf = serializers.FileField(required=False)
    adjuntos = serializers.ListField(
        child=serializers.FileField(), required=False, allow_empty=True,
    )

    def validate(self, attrs):
        archivos = [a for a in (attrs.get("pdf"),) if a is not None]
        archivos += list(attrs.get("adjuntos") or [])
        tamano = sum(archivo.size for archivo in archivos)
        if tamano > TAMANO_MAXIMO_ADJUNTOS:
            raise serializers.ValidationError(
                {"adjuntos": mensaje_adjuntos_muy_pesados(tamano)}
            )
        return attrs
