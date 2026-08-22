"""Serializer del emisor."""
from rest_framework import serializers

from apps.catalogos.models import (
    Departamento,
    Municipio,
    Pais,
    ResponsabilidadFiscal,
)
from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor
from apps.seguridad.alcance import MENSAJE_FUERA_DE_CUENTA, cuenta_de_la_credencial

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


class CodigoDeCatalogo(serializers.SlugRelatedField):
    """Campo de catálogo que entra y sale por su ``codigo``, no por el ``id``.

    El id es un serial de la base y no significa nada fuera de ella: el mismo
    municipio tiene distinto id en desarrollo y en producción, según en qué
    orden se cargaron las listas. El código sí es estable y es el que el ERP
    conoce —ISO 3166 para el país, DANE para departamento (2 dígitos) y
    municipio (5)—, así que es lo que se recibe y lo que se devuelve.
    """

    default_error_messages = {
        "does_not_exist": "No existe en el catálogo el código '{value}'.",
        "invalid": "Se espera el código del catálogo, no el id.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("slug_field", "codigo")
        super().__init__(**kwargs)


# Nombra la cuenta a propósito: el mismo NIT puede estar dado de alta en otra
# (ver Emisor.Meta.constraints), así que "ya existe" sin decir dónde parece un
# error de la plataforma. Formatear con `.format(numero=..., cuenta=...)`.
MENSAJE_DUPLICADO = (
    "El emisor con identificación {numero} ya existe en la cuenta '{cuenta}'."
)


class EmisorSerializer(serializers.ModelSerializer):
    resoluciones = ResolucionFacturacionSerializer(many=True, read_only=True)
    # No se exige en el cuerpo: para una integración sale de la credencial; solo
    # el staff de la plataforma la indica explícitamente.
    # Solo cuentas activas: una cuenta desactivada no admite emisores nuevos.
    cuenta = serializers.PrimaryKeyRelatedField(
        queryset=Cuenta.objects.filter(activa=True), default=CuentaDeLaCredencial()
    )
    # La ubicación llega por código; el serializer resuelve la fila y guarda su
    # llave, que es lo que la FK necesita.
    pais = CodigoDeCatalogo(queryset=Pais.objects.all())
    departamento = CodigoDeCatalogo(queryset=Departamento.objects.all())
    municipio = CodigoDeCatalogo(queryset=Municipio.objects.all())
    # Igual que la ubicación: por su código de la lista TipoResponsabilidad
    # ('O-13', 'O-15', 'O-23', 'O-47', 'R-99-PN'), que es lo que viaja en el
    # TaxLevelCode del XML y lo que el ERP conoce. Sin ninguna, el XML sale con
    # 'R-99-PN' (ver `SIN_RESPONSABILIDAD` en apps.dian.ubl).
    responsabilidades = CodigoDeCatalogo(
        queryset=ResponsabilidadFiscal.objects.all(), many=True, required=False,
    )
    # Lo marca el envío del Set de Pruebas, no el cuerpo de la petición:
    # decir 'ya estoy habilitado' no habilita a nadie.
    habilitado_facturacion = serializers.BooleanField(read_only=True)

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

    def validate(self, attrs):
        """Comprueba que el emisor no esté ya dado de alta en su cuenta.

        El NIT no se contrasta con el RUES al dar de alta: es un registro ajeno
        y a veces caído, y no puede decidir si un alta entra o no. Quien quiera
        comprobarlo tiene ``GET /api/emisores/emisor/validar-nit/``, que además
        devuelve los datos para autocompletar el formulario.
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

        self.exigir_no_duplicado(cuenta, tipo, numero)
        return attrs

    def exigir_no_duplicado(self, cuenta, tipo, numero):
        """La unicidad del emisor dentro de su cuenta.

        Sustituye al ``UniqueTogetherValidator`` que DRF genera solo (ver
        ``Meta.validators``), cuyo mensaje nombra ``cuenta`` —un campo que el ERP
        nunca envía— y aterriza en ``non_field_errors``, donde el formulario no
        lo puede señalar. Aquí el error cuelga de ``numero_identificacion``, que
        es el campo que hay que corregir.
        """
        repetidos = Emisor.objects.filter(
            cuenta=cuenta, tipo_identificacion=tipo, numero_identificacion=numero
        )
        if self.instance is not None:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        if repetidos.exists():
            raise serializers.ValidationError(
                {
                    "numero_identificacion": MENSAJE_DUPLICADO.format(
                        numero=numero, cuenta=cuenta.nombre
                    )
                }
            )

    class Meta:
        model = Emisor
        fields = [
            "id", "cuenta", "razon_social", "nombre_comercial",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion",
            "telefono", "correo", "activo", "habilitado_facturacion",
            "resoluciones",
        ]
        # Vacío a propósito: desactiva el UniqueTogetherValidator automático de
        # DRF para que la unicidad la explique `validate()` con un mensaje útil.
        validators = []
