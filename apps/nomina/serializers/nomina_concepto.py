"""Serializer de los conceptos (devengados y deducciones) de una nómina."""
from rest_framework import serializers

from apps.nomina.models import NominaConcepto


class NominaConceptoSerializer(serializers.ModelSerializer):
    """Un devengado o una deducción. Va anidado en la nómina.

    ``concepto`` es el discriminador interno, no un código de la DIAN: el anexo
    no numera los conceptos, los distingue por el nombre del elemento XML. Los
    valores admitidos son los de ``NominaConcepto.Concepto``.
    """

    concepto_nombre = serializers.CharField(source="get_concepto_display", read_only=True)

    class Meta:
        model = NominaConcepto
        fields = [
            "id", "grupo", "concepto", "concepto_nombre",
            "cantidad", "porcentaje", "valor", "valor_no_salarial",
            "fecha_inicio", "fecha_fin", "hora_inicio", "hora_fin",
            "descripcion", "tipo_incapacidad",
        ]
