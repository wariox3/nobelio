"""Serializer del emisor."""
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.emisores.models import Emisor
from apps.utilidades.rues import RuesNoDisponible, consultar_nit

from .resolucion import ResolucionFacturacionSerializer


class RuesNoDisponibleError(APIException):
    """503: no se pudo verificar el NIT contra el RUES (no es 'no existe')."""

    status_code = 503
    default_detail = (
        "No se pudo validar el NIT contra el RUES en este momento. "
        "Intente más tarde."
    )
    default_code = "rues_no_disponible"


class EmisorSerializer(serializers.ModelSerializer):
    resoluciones = ResolucionFacturacionSerializer(many=True, read_only=True)

    def validate_numero_identificacion(self, value):
        """Exige que el NIT exista en el RUES al crear/cambiar el emisor.

        - No existe en el RUES -> 400 (error de validación del campo).
        - RUES no disponible    -> 503 (no se pudo verificar).
        """
        # En edición, si el NIT no cambió, no se vuelve a consultar el RUES.
        if self.instance and self.instance.numero_identificacion == value:
            return value
        try:
            empresa = consultar_nit(value)
        except RuesNoDisponible as exc:
            raise RuesNoDisponibleError() from exc
        if empresa is None:
            raise serializers.ValidationError(
                "El NIT no existe en el RUES (Registro Único Empresarial y Social)."
            )
        return value

    class Meta:
        model = Emisor
        fields = [
            "id", "cuenta", "razon_social", "nombre_comercial",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion",
            "telefono", "correo", "activo", "resoluciones",
        ]
