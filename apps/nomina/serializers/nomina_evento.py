"""Serializer de los eventos de una nómina."""
from rest_framework import serializers

from apps.nomina.models import NominaEvento


class NominaEventoSerializer(serializers.ModelSerializer):
    class Meta:
        model = NominaEvento
        fields = ["id", "nomina", "tipo", "datos", "fecha"]
        read_only_fields = fields
