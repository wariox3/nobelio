"""Serializers de detalles de documento e impuestos por detalle."""
from decimal import ROUND_HALF_UP, Decimal

from rest_framework import serializers

from apps.documentos.models import DocumentoDetalleImpuesto, DocumentoDetalle
from apps.catalogos.memoria import RelacionDeCatalogo
from apps.nucleo.serializers import EstructuraEstricta

CERO = Decimal("0")
# Dos redondeos legítimos a dos decimales pueden separarse un céntimo y no más;
# es la misma tolerancia que la regla de impuestos del documento.
TOLERANCIA_TOTAL_LINEA = Decimal("0.01")

MENSAJE_PERIODO_AL_REVES = (
    "El fin del periodo facturado tiene que ser posterior a su inicio "
    "(`periodo_desde`)."
)
# Código propio: el `min_value` de DRF es "mayor o igual", y aquí el cero
# tampoco vale.
CODIGO_MAYOR_QUE_CERO = "mayor_que_cero"
MENSAJE_MAYOR_QUE_CERO = "Tiene que ser mayor que cero."


def mensaje_total_linea_descuadrado(esperado, informado):
    """Mensaje para una línea cuyo total no es cantidad × precio − descuento."""
    return (
        f"No cuadra: cantidad × valor unitario − descuento da {esperado} y se "
        f"informó {informado}."
    )


def _mayor_que_cero(valor):
    if valor <= CERO:
        raise serializers.ValidationError(MENSAJE_MAYOR_QUE_CERO, code=CODIGO_MAYOR_QUE_CERO)
    return valor


class DocumentoDetalleImpuestoSerializer(EstructuraEstricta, serializers.ModelSerializer):
    # El mismo tributo se repite en casi todas las líneas: sale de la memoria de
    # catálogos, o se busca una sola vez por petición.
    serializer_related_field = RelacionDeCatalogo
    tributo_codigo = serializers.CharField(source="tributo.codigo", read_only=True)

    class Meta:
        model = DocumentoDetalleImpuesto
        fields = ["id", "tributo", "tributo_codigo", "base_gravable", "tarifa", "valor"]
        # El modelo los deja en cero por defecto, y así un importe que no venía
        # se guardaba como un cero que nadie había informado. Se exigen en la
        # petición: cero es un valor que se manda, no lo que queda si se olvida.
        #
        # Ninguno negativo. La tarifa y el valor sí pueden ser cero —el IVA
        # exento va al 0 %—; la base no (`validate_base_gravable`).
        extra_kwargs = {
            "base_gravable": {"required": True},
            "tarifa": {"required": True, "min_value": CERO},
            "valor": {"required": True, "min_value": CERO},
        }

    def validate_base_gravable(self, valor):
        return _mayor_que_cero(valor)


class DocumentoDetalleSerializer(EstructuraEstricta, serializers.ModelSerializer):
    # Y la misma unidad de medida.
    serializer_related_field = RelacionDeCatalogo
    impuestos = DocumentoDetalleImpuestoSerializer(many=True)

    class Meta:
        model = DocumentoDetalle
        fields = [
            "id", "numero_linea", "descripcion", "codigo_producto",
            "cantidad", "unidad_medida", "valor_unitario", "valor_total",
            "descuento", "descuento_motivo", "impuestos",
            # Datos de negocio: opcionales, viajan al XML si vienen.
            "nota", "marca", "modelo", "centro_costo",
            "periodo_desde", "periodo_hasta",
            "periodo_descripcion", "periodo_descripcion_codigo",
        ]
        # Como en el impuesto: los importes de la línea no se pueden omitir, el
        # `descuento` tampoco —entra en el total de la línea, así que sin
        # descuento se manda 0—. Cantidad, precio y total tienen que ser
        # mayores que cero (`validate_*`); el descuento puede ser cero, no
        # negativo.
        #
        # `codigo_producto` también, y con valor: sin él el XML identificaba el
        # ítem (`StandardItemIdentification`) con el número de línea, un código
        # que no significa nada para el comprador ni se repite entre facturas.
        extra_kwargs = {
            **{
                campo: {"required": True}
                for campo in ("cantidad", "valor_unitario", "valor_total")
            },
            "descuento": {"required": True, "min_value": CERO},
            "codigo_producto": {"required": True, "allow_blank": False},
        }

    def validate_cantidad(self, valor):
        return _mayor_que_cero(valor)

    def validate_valor_unitario(self, valor):
        return _mayor_que_cero(valor)

    def validate_valor_total(self, valor):
        return _mayor_que_cero(valor)

    def validate(self, attrs):
        """El total de la línea y su periodo.

        **Total.** ``valor_total`` es el ``LineExtensionAmount`` de la línea:
        cantidad × valor unitario − descuento, que es lo que ya decía la ayuda
        del campo. Antes no se comprobaba, y una línea descuadrada se sumaba al
        valor bruto y se firmaba igual. Se compara en vez de calcularse, por lo
        mismo que los impuestos: quien liquida es el ERP, y un descuadre es un
        error suyo que hay que sacar a la luz.

        **Periodo.** Si trae los dos extremos, va hacia adelante. Estricto por
        decisión de MarioA: el inicio tiene que ser anterior al fin, así que un
        periodo de un solo día (las dos fechas iguales) tampoco pasa.
        """
        errores = {}

        esperado = (
            attrs["cantidad"] * attrs["valor_unitario"] - attrs["descuento"]
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if abs(esperado - attrs["valor_total"]) > TOLERANCIA_TOTAL_LINEA:
            errores["valor_total"] = mensaje_total_linea_descuadrado(
                esperado, attrs["valor_total"],
            )

        desde, hasta = attrs.get("periodo_desde"), attrs.get("periodo_hasta")
        if desde and hasta and not desde < hasta:
            errores["periodo_hasta"] = MENSAJE_PERIODO_AL_REVES

        if errores:
            raise serializers.ValidationError(errores)
        return attrs
