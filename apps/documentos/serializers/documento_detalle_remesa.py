"""Serializer de la remesa de transporte que factura una línea.

Factura de transporte de carga, tipo de operación 12 (anexo FE 1.9, numeral
19.2). Cada remesa registrada en el RNDC va en su propia línea, con tres
``cac:AdditionalItemProperty`` (lista ``RemesaTransporte``):

- ``01``: número de radicación de la remesa que entregó el RNDC.
- ``02``: consecutivo interno de la remesa en la empresa de transporte.
- ``03``: valor del flete, con la cantidad transportada en ``cbc:ValueQuantity``.

El RNDC lee el XML que aceptó la DIAN para cumplir la remesa, así que lo que se
valida aquí es lo que el anexo dice de cada dato.
"""
import re

from rest_framework import serializers

from apps.nucleo.serializers import EstructuraEstricta

# «Dato numérico de 1 hasta 100,000 millones» (numeral 19.2, punto 1).
RADICADO_MAXIMO = 100_000_000_000
# «Alfanumérico de hasta 15 caracteres» (punto 2).
PATRON_CONSECUTIVO = re.compile(r"^[0-9A-Za-z-]{1,15}$")
# Las dos unidades que admite el punto 4: kilogramos y galones.
UNIDADES = (("KGM", "Kilogramos"), ("GLL", "Galones"))

MENSAJE_CONSECUTIVO_INVALIDO = (
    "Debe ser alfanumérico (letras, números y guion), de hasta 15 caracteres."
)


class DocumentoDetalleRemesaSerializer(EstructuraEstricta, serializers.Serializer):
    """La remesa del RNDC que factura la línea.

    El flete y la cantidad son enteros: el anexo los define «sin decimales».
    """

    radicado = serializers.IntegerField(min_value=1, max_value=RADICADO_MAXIMO)
    consecutivo = serializers.CharField(max_length=15)
    valor_flete = serializers.IntegerField(min_value=1)
    cantidad = serializers.IntegerField(min_value=1)
    unidad = serializers.ChoiceField(choices=UNIDADES)

    def validate_consecutivo(self, valor):
        if not PATRON_CONSECUTIVO.match(valor):
            raise serializers.ValidationError(MENSAJE_CONSECUTIVO_INVALIDO)
        return valor
