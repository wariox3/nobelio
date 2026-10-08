"""Serializers de la carga manual de documentos recibidos."""
from pathlib import PurePosixPath

from rest_framework import serializers

from apps.emisores.models import Emisor
from apps.seguridad.alcance import RelacionDelAlcance

from .documento import DocumentoRecibidoResumenSerializer

EXTENSIONES_CARGA = {".zip", ".xml"}
# Un ZIP de factura con su PDF pesa unos cientos de KB; el anexo pone 2 MB
# como tope del correo. 10 MB deja holgura sin abrir la puerta a cualquier cosa.
MAXIMO_CARGA = 10 * 1024 * 1024


class CargaDocumentoSerializer(serializers.Serializer):
    """La entrada de ``POST /api/recepcion/documento/cargar/``."""

    # Acotado al alcance: un emisor ajeno responde igual que uno inexistente.
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    archivo = serializers.FileField(
        help_text="El ZIP que manda el proveedor (AttachedDocument y PDF) o el XML suelto.",
    )

    def validate_archivo(self, archivo):
        extension = PurePosixPath((archivo.name or "").lower()).suffix
        if extension not in EXTENSIONES_CARGA:
            raise serializers.ValidationError(
                "Solo se aceptan archivos .zip o .xml."
            )
        if archivo.size > MAXIMO_CARGA:
            raise serializers.ValidationError(
                f"El archivo pesa más de {MAXIMO_CARGA // (1024 * 1024)} MB."
            )
        return archivo


class RechazoCargaSerializer(serializers.Serializer):
    numero = serializers.CharField()
    cufe_cude = serializers.CharField()
    receptor_numero_identificacion = serializers.CharField()
    motivo = serializers.CharField()


class ResultadoCargaSerializer(serializers.Serializer):
    """Qué pasó con cada documento del archivo.

    ``creados`` son los nuevos; ``repetidos``, los que ya estaban registrados
    (por correo o por otra carga); ``rechazados``, los de otro receptor.
    """

    creados = DocumentoRecibidoResumenSerializer(many=True)
    repetidos = DocumentoRecibidoResumenSerializer(many=True)
    rechazados = RechazoCargaSerializer(many=True)
