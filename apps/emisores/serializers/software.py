"""Serializer del software DIAN del emisor."""
from rest_framework import serializers

from apps.emisores.models import SoftwareDian
from apps.emisores.servicios import motivo_no_puede_emitir


class SoftwareDianSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftwareDian
        fields = [
            "id", "emisor", "tipo", "identificador", "pin",
            "test_set_id", "set_pruebas_aceptado", "activo",
            # Solo los usa el documento equivalente, pero se exponen siempre:
            # son campos del software y esconderlos según el tipo daría un
            # contrato que cambia de forma sin que nada lo anuncie. En los
            # softwares de facturación y nómina se quedan vacíos.
            "codigo_proveedor_tecnologico",
            "fabricante_nombre", "fabricante_razon_social",
            "fabricante_nombre_software",
        ]
        # El PIN se devuelve en el listado y en el detalle a petición de MarioA
        # (2026-08-28). Antes era `write_only`: se aceptaba al crear y no salía
        # nunca. Es un dato sensible —entra en el SoftwareSecurityCode y en el
        # CUDE/CUNE—, así que lo que lo contiene es el alcance: `AlcanceEmisorMixin`
        # limita el queryset a los emisores que alcanza quien pregunta, de modo
        # que una cuenta no ve los softwares de otra.

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