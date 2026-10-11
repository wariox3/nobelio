"""Serializer de resoluciones de numeración."""
from rest_framework import serializers

from apps.emisores.models import Resolucion


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
            "prefijo", "rango_desde", "rango_hasta", "consecutivo_actual",
            "vigente_desde", "vigente_hasta",
            "clave_tecnica", "activa",
        ]


class ResolucionAValidarSerializer(serializers.Serializer):
    """Una resolución como la tiene quien llama, para contrastarla."""

    numero_resolucion = serializers.CharField(max_length=50)
    prefijo = serializers.CharField(
        max_length=10, required=False, allow_blank=True,
    )
    rango_desde = serializers.IntegerField(required=False, min_value=0)
    rango_hasta = serializers.IntegerField(required=False, min_value=0)
    vigente_desde = serializers.DateField(required=False)
    vigente_hasta = serializers.DateField(required=False)


class ValidarResolucionesSerializer(serializers.Serializer):
    """Lo que recibe ``validar``: el emisor y sus resoluciones a contrastar."""

    emisor = serializers.IntegerField()
    resoluciones = ResolucionAValidarSerializer(many=True, allow_empty=False)


class ResolucionValidadaSerializer(serializers.Serializer):
    """El resultado de ``validar`` para una resolución.

    ``prefijo``, los rangos y las vigencias son los registrados, no los que se
    enviaron; van en ``null`` cuando el emisor no tiene la resolución.
    """

    numero_resolucion = serializers.CharField()
    validado = serializers.BooleanField()
    inconsistencia = serializers.BooleanField()
    prefijo = serializers.CharField(allow_null=True, allow_blank=True)
    rango_desde = serializers.IntegerField(allow_null=True)
    rango_hasta = serializers.IntegerField(allow_null=True)
    vigente_desde = serializers.DateField(allow_null=True)
    vigente_hasta = serializers.DateField(allow_null=True)
