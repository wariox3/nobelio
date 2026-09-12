"""Serializer del certificado digital del emisor."""
import os

from rest_framework import serializers

from apps.emisores.models import Certificado, Emisor


class CertificadoSerializer(serializers.ModelSerializer):
    # Solo el nombre del archivo, para que la UI sepa que hay un .p12 cargado
    # sin exponer un enlace de descarga al certificado (es sensible).
    nombre_archivo = serializers.SerializerMethodField()

    # Sensible: se acepta al subir pero nunca se devuelve. Se declara aquí en
    # vez de en `extra_kwargs` por el `max_length`, que no puede heredarse del
    # modelo: la columna es de 512 para que quepa el token cifrado, no para
    # admitir claves de 512 caracteres —una de ese tamaño, cifrada, ya no
    # cabría—. El límite de entrada sigue siendo el de siempre.
    clave = serializers.CharField(write_only=True, max_length=255)

    # `validators=[]` desactiva el UniqueValidator que DRF le pone solo por ser
    # el lado OneToOne. No es que sobre la comprobación —cada emisor tiene un
    # certificado— sino que se adelanta a la de `cargar` y contesta "certificado
    # with this emisor already exists", en inglés y sin decir qué hacer. La
    # misma regla la explica la vista, con el id que hay que borrar.
    emisor = serializers.PrimaryKeyRelatedField(
        queryset=Emisor.objects.all(), validators=[],
    )

    class Meta:
        model = Certificado
        fields = [
            "id", "emisor", "alias", "archivo", "nombre_archivo", "clave",
            "vigente_desde", "vigente_hasta",
        ]
        extra_kwargs = {
            # Sensible: se acepta al subir pero nunca se devuelve.
            "archivo": {"write_only": True},
        }

    def get_nombre_archivo(self, obj) -> str:
        return os.path.basename(obj.archivo.name) if obj.archivo else ""
