"""Serializer del emisor."""
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor
from apps.seguridad.alcance import MENSAJE_FUERA_DE_CUENTA, cuenta_de_la_credencial
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
    # Solo cuentas activas: una cuenta desactivada no admite emisores nuevos.
    cuenta = serializers.PrimaryKeyRelatedField(
        queryset=Cuenta.objects.filter(activa=True), default=CuentaDeLaCredencial()
    )

    def validate_cuenta(self, value):
        """Una integración no puede operar fuera de su propia cuenta.

        Se responde 400 (y no el 403 de ``alcance.exigir_cuenta``) porque aquí
        el problema es un campo del cuerpo que contradice a la credencial; el
        403 lo da la vista, que es la última puerta antes de escribir.
        """
        propia = _cuenta_de(self.context)
        if propia is not None and value != propia:
            raise serializers.ValidationError(MENSAJE_FUERA_DE_CUENTA)
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

    def validate(self, attrs):
        """Comprueba la unicidad del emisor dentro de su cuenta.

        Sustituye al ``UniqueTogetherValidator`` que DRF genera solo (ver
        ``Meta.validators``), cuyo mensaje nombra ``cuenta`` —un campo que el ERP
        nunca envía— y aterriza en ``non_field_errors``, donde el formulario no
        lo puede señalar. Aquí el error cuelga de ``numero_identificacion``, que
        es el campo que hay que corregir, y dice de quién es el registro que
        estorba para que se vea que no hay que crearlo, sino usarlo.
        """
        def valor(campo):
            return attrs.get(campo, getattr(self.instance, campo, None))

        cuenta = valor("cuenta")
        tipo = valor("tipo_identificacion")
        numero = valor("numero_identificacion")
        if not (cuenta and tipo and numero):
            # Falta algo para poder comprobarlo: ya lo reportan los validadores
            # de campo (o la vista, si es la cuenta del staff).
            return attrs

        repetidos = Emisor.objects.filter(
            cuenta=cuenta, tipo_identificacion=tipo, numero_identificacion=numero
        )
        if self.instance is not None:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        existente = repetidos.first()
        if existente is not None:
            raise serializers.ValidationError(
                {
                    "numero_identificacion": (
                        f"La cuenta '{cuenta}' ya tiene un emisor con "
                        f"{tipo.nombre} {numero}: '{existente.razon_social}'. "
                        f"Use ese emisor (id {existente.pk}) en vez de crear otro."
                    )
                }
            )
        return attrs

    class Meta:
        model = Emisor
        fields = [
            "id", "cuenta", "razon_social", "nombre_comercial",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion",
            "telefono", "correo", "activo", "resoluciones",
        ]
        # Vacío a propósito: desactiva el UniqueTogetherValidator automático de
        # DRF para que la unicidad la explique `validate()` con un mensaje útil.
        validators = []
