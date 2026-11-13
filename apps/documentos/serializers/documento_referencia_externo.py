"""Serializer del documento corregido que se emitió fuera de nobelio."""
import re

from rest_framework import serializers

from apps.nucleo.serializers import EstructuraEstricta

# CUFE, CUDE y CUDS son un SHA-384 en hexadecimal: 96 caracteres.
PATRON_CUFE_CUDE = re.compile(r"^[0-9a-fA-F]{96}$")
MENSAJE_CUFE_CUDE_INVALIDO = (
    "Debe ser el CUFE, CUDE o CUDS del documento: 96 caracteres hexadecimales "
    "(SHA-384)."
)


class DocumentoReferenciaExternoSerializer(EstructuraEstricta, serializers.Serializer):
    """Documento que corrige la nota cuando se emitió en otro sistema.

    Son los datos con que la DIAN lo conoce y los únicos que el XML de la nota
    necesita de él: el número (``cbc:ReferenceID`` y ``cbc:ID``), su
    CUFE/CUDE/CUDS (``cbc:UUID``) y su fecha de emisión (``cbc:IssueDate``).
    Como no está en nobelio, no se comprueba que exista ni que esté aceptado:
    eso lo valida la DIAN al recibir la nota.
    """

    numero = serializers.CharField(max_length=30)
    cufe_cude = serializers.CharField(max_length=96)
    fecha = serializers.DateField()

    def validate_cufe_cude(self, valor):
        if not PATRON_CUFE_CUDE.match(valor):
            raise serializers.ValidationError(MENSAJE_CUFE_CUDE_INVALIDO)
        # Tal cual llega: el UUID del XML tiene que coincidir con el que la DIAN
        # registró para ese documento.
        return valor

    def to_internal_value(self, data):
        # Se guarda en un JSONField: la fecha va como texto ISO, que es lo que
        # `Documento.referencia_corregida` vuelve a leer.
        datos = super().to_internal_value(data)
        datos["fecha"] = datos["fecha"].isoformat()
        return datos
