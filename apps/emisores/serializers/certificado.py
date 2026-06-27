"""Serializer del certificado digital del emisor."""
import os

from rest_framework import serializers

from apps.emisores.models import Certificado


class CertificadoSerializer(serializers.ModelSerializer):
    # Solo el nombre del archivo, para que la UI sepa que hay un .p12 cargado
    # sin exponer un enlace de descarga al certificado (es sensible).
    nombre_archivo = serializers.SerializerMethodField()

    class Meta:
        model = Certificado
        fields = [
            "id", "emisor", "alias", "archivo", "nombre_archivo", "clave",
            "vigente_desde", "vigente_hasta", "activo",
        ]
        extra_kwargs = {
            # Sensibles: se aceptan al subir/editar pero nunca se devuelven.
            "archivo": {"write_only": True},
            "clave": {"write_only": True},
        }

    def get_nombre_archivo(self, obj) -> str:
        return os.path.basename(obj.archivo.name) if obj.archivo else ""
