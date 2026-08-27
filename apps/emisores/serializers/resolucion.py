"""Serializer de resoluciones de numeración."""
from rest_framework import serializers

from apps.emisores.models import (
    Resolucion,
    mensaje_resolucion_ocupada,
    resolucion_activa_en_otra_cuenta,
)


class ResolucionSerializer(serializers.ModelSerializer):
    # La clave técnica es sensible: se puede escribir (necesaria para el CUFE)
    # pero nunca se devuelve en las respuestas.
    clave_tecnica = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = Resolucion
        fields = [
            "id", "emisor", "tipo_factura", "numero_resolucion", "fecha_resolucion",
            "prefijo", "rango_desde", "rango_hasta", "vigente_desde", "vigente_hasta",
            "clave_tecnica", "consecutivo_actual", "activa",
        ]

    def validate(self, attrs):
        """Impide que dos cuentas numeren a la vez con la misma resolución."""
        def dato(campo, por_defecto=None):
            if campo in attrs:
                return attrs[campo]
            return getattr(self.instance, campo, por_defecto)

        # Al crear sin indicarla, el modelo la deja activa (default=True).
        if not dato("activa", por_defecto=True):
            # Una resolución inactiva no numera: no puede chocar con nadie.
            return attrs

        emisor = dato("emisor")
        ocupada = resolucion_activa_en_otra_cuenta(
            emisor,
            prefijo=dato("prefijo") or "",
            numero_resolucion=dato("numero_resolucion"),
            excluir_pk=self.instance.pk if self.instance else None,
        )
        if ocupada is not None:
            raise serializers.ValidationError(
                {"numero_resolucion": mensaje_resolucion_ocupada(ocupada)}
            )
        return attrs
