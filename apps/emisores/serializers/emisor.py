"""Serializer del emisor."""
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor
from apps.seguridad.alcance import cuenta_de_la_credencial
from apps.utilidades.rues import RuesNoDisponible, consultar_nit

from .resolucion import ResolucionFacturacionSerializer


class CuentaDeLaCredencial:
    """Default de ``cuenta``: la de la API Key que hace la petición.

    Hace falta como *default* y no solo en la vista porque la unicidad del
    emisor es ``(cuenta, tipo_identificacion, numero_identificacion)``: DRF
    valida ese conjunto antes de que la vista toque nada, así que necesita la
    cuenta ya resuelta. Para el staff devuelve ``None`` y la indica en el cuerpo.
    """

    requires_context = True

    def __call__(self, campo):
        return _cuenta_de(campo.context)


def _cuenta_de(contexto):
    """Cuenta de la credencial, o ``None`` fuera de una petición HTTP."""
    request = contexto.get("request")
    return cuenta_de_la_credencial(request) if request is not None else None


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
    # No se exige en el cuerpo: para una integración sale de la credencial; solo
    # el staff de la plataforma la indica explícitamente.
    cuenta = serializers.PrimaryKeyRelatedField(
        queryset=Cuenta.objects.all(), default=CuentaDeLaCredencial()
    )

    def validate_cuenta(self, value):
        """Una integración no puede crear emisores fuera de su propia cuenta."""
        propia = _cuenta_de(self.context)
        if propia is not None and value != propia:
            raise serializers.ValidationError(
                "La credencial solo puede operar sobre su propia cuenta."
            )
        return value

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
