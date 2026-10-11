"""Serializer de resoluciones de numeración."""
from rest_framework import serializers

from apps.emisores.models import Emisor, Resolucion, SoftwareDian
from apps.seguridad.alcance import RelacionDelAlcance


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


class ValidarResolucionSerializer(serializers.Serializer):
    """Lo que recibe ``validar``: de quién, con qué software y qué número."""

    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    tipo_software = serializers.ChoiceField(choices=SoftwareDian.Tipo.choices)
    numero_resolucion = serializers.CharField(max_length=50)
