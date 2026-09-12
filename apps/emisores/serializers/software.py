"""Serializer del software DIAN del emisor."""
from rest_framework import serializers

from apps.emisores.models import SoftwareDian
from apps.emisores.servicios import motivo_no_puede_emitir


class SoftwareDianSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftwareDian
        fields = [
            "id", "emisor", "tipo", "identificador", "pin",
            "test_set_id", "set_pruebas_aceptado",
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
        #
        # Vacío a propósito: desactiva el UniqueTogetherValidator que DRF saca
        # solo del `UniqueConstraint(emisor, tipo)`. La regla es la misma, pero
        # su mensaje —"The fields emisor, tipo must make a unique set"— ni está
        # en español ni dice qué hacer. La explica `validate()`, con la ruta
        # del software que ya existe.
        validators = []

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
        self.exigir_uno_por_tipo(emisor, attrs)
        return attrs

    def exigir_uno_por_tipo(self, emisor, attrs):
        """Un software por emisor y operación; para cambiarlo se actualiza.

        La DIAN habilita facturación, nómina y documento equivalente por
        separado, así que los tres conviven; lo que no cabe es un segundo de la
        misma operación. Dos filas del mismo tipo solo servirían para dudar de
        cuál es la buena, y cada una guarda un PIN vivo.
        """
        tipo = attrs.get("tipo") or getattr(self.instance, "tipo", None)
        if tipo is None:
            return  # Sin tipo no hay nada que comprobar; ya lo exige el campo.

        otros = SoftwareDian.objects.filter(emisor=emisor, tipo=tipo)
        if self.instance is not None:
            # En un PUT/PATCH, chocar consigo mismo no es un duplicado.
            otros = otros.exclude(pk=self.instance.pk)
        existente = otros.first()
        if existente is None:
            return

        etiqueta = SoftwareDian.Tipo(tipo).label.lower()
        raise serializers.ValidationError({
            "tipo": (
                f"El emisor ya tiene un software DIAN de {etiqueta}. Cada "
                f"operación admite uno: actualice el que hay (PATCH "
                f"/api/emisores/software/{existente.id}/) en vez de registrar "
                f"otro."
            )
        })