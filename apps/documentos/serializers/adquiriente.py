"""Serializer del adquiriente: va anidado dentro del documento."""
from rest_framework import serializers

from apps.documentos.models import Adquiriente
from apps.nucleo.serializers import EstructuraEstricta


class AdquirienteSerializer(EstructuraEstricta, serializers.ModelSerializer):
    """Datos del receptor. No tiene endpoint propio: se piden en el documento."""

    class Meta:
        model = Adquiriente
        fields = [
            "razon_social",
            "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "tipo_identificacion", "numero_identificacion",
            "digito_verificacion", "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "codigo_postal",
            "telefono", "correo",
        ]
        # El código postal se exige siempre, aunque el modelo lo admita vacío:
        # es el `cbc:PostalZone` del XML y no hay forma de completarlo después
        # —un documento firmado ya no se edita—, así que se pide al crear y no
        # cuando la DIAN lo rechace con el consecutivo ya gastado.
        #
        # El dígito de verificación es de solo lectura: lo calcula
        # `Adquiriente.save` a partir del NIT, y el que mandara el ERP se
        # sobrescribía sin avisar. Mandarlo responde 400; en la lectura sale el
        # calculado.
        extra_kwargs = {
            "codigo_postal": {"required": True, "allow_blank": False},
            "digito_verificacion": {"read_only": True},
        }
