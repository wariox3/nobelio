"""Serializers de lectura y creación del documento electrónico."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from apps.documentos import models
from apps.emisores.models import Emisor, ResolucionFacturacion
from apps.emisores.servicios import motivo_no_puede_emitir
from apps.seguridad.alcance import RelacionDelAlcance

from .adquiriente import AdquirienteSerializer
from .documento_detalle import DocumentoDetalleSerializer
from .documento_error import DocumentoErrorSerializer

# El emisor solo conoce el número que le dio la DIAN, no nuestros ids, así que
# el documento se crea con `numero_resolucion` y aquí se traduce a la fila.
MENSAJE_RESOLUCION_NO_ENCONTRADA = (
    "El emisor no tiene una resolución activa con ese número."
)
MENSAJE_RESOLUCION_AMBIGUA = (
    "El emisor tiene varias resoluciones activas con ese número; el prefijo del "
    "documento no distingue cuál usar."
)
MENSAJE_DOCUMENTO_NO_EDITABLE = (
    "El documento ya fue firmado y no se puede modificar."
)


def mensaje_fecha_emision_no_es_hoy(hoy):
    """Mensaje para una fecha de emisión que no es la de hoy.

    La regla FAD09 de la DIAN exige que la fecha de generación coincida con la
    de la firma, y se firma en el momento de emitir: cualquier otra fecha
    produce un rechazo que ya cuesta un consecutivo.
    """
    return (
        f"La fecha de emisión debe ser la de hoy ({hoy}): la DIAN rechaza "
        "(regla FAD09) los documentos cuya fecha de generación no coincide "
        "con la fecha de la firma."
    )


def mensaje_prefijo_ajeno(resolucion):
    """Mensaje para un prefijo que no es el que autorizó la resolución."""
    prefijo = f"'{resolucion.prefijo}'" if resolucion.prefijo else "sin prefijo"
    return (
        f"La resolución {resolucion.numero_resolucion} numera {prefijo}, "
        "no con este prefijo."
    )


def mensaje_consecutivo_fuera_de_rango(resolucion):
    """Mensaje para un consecutivo fuera del rango autorizado."""
    return (
        f"Está fuera del rango autorizado por la resolución "
        f"{resolucion.numero_resolucion} ({resolucion.rango_desde} a "
        f"{resolucion.rango_hasta})."
    )


class DocumentoSerializer(serializers.ModelSerializer):
    """Serializer de lectura del documento, con detalles anidados."""

    detalles = DocumentoDetalleSerializer(many=True, read_only=True)
    errores = DocumentoErrorSerializer(many=True, read_only=True)
    adquiriente = AdquirienteSerializer(read_only=True)
    documento_tipo_nombre = serializers.CharField(
        source="documento_tipo.nombre", read_only=True
    )
    estado_nombre = serializers.CharField(source="estado.nombre", read_only=True)
    estado_descripcion = serializers.CharField(source="estado.descripcion", read_only=True)
    # El número es lo que el emisor reconoce de su resolución; el id es interno.
    resolucion_numero = serializers.CharField(
        source="resolucion.numero_resolucion", read_only=True, default=None,
    )

    class Meta:
        model = models.Documento
        fields = [
            "id", "documento_tipo", "documento_tipo_nombre",
            "estado", "estado_nombre", "estado_descripcion",
            "emisor", "resolucion", "resolucion_numero", "adquiriente",
            "prefijo", "consecutivo", "numero", "cufe_cude", "track_id",
            "envio", "ambiente", "fecha_validacion", "errores",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "valor_bruto", "total_impuestos", "total_descuentos", "total_cargos",
            "total_a_pagar", "documento_referencia", "observaciones", "detalles",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = [
            "estado", "cufe_cude", "track_id", "envio", "ambiente", "fecha_validacion",
            "valor_bruto", "total_impuestos",
            "total_a_pagar", "creado_en", "actualizado_en",
        ]


class DocumentoListaSerializer(DocumentoSerializer):
    """Versión para el listado: sin las líneas anidadas y, del estado, solo el nombre."""

    detalles = None  # se quita el campo heredado
    estado_descripcion = None  # en la lista solo va estado_nombre

    class Meta(DocumentoSerializer.Meta):
        fields = [
            f for f in DocumentoSerializer.Meta.fields
            if f not in {"detalles", "estado_descripcion"}
        ]
        read_only_fields = [
            f for f in DocumentoSerializer.Meta.read_only_fields
            if f not in {"detalles", "estado_descripcion"}
        ]


class DocumentoCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con detalles e impuestos anidados.

    Calcula automáticamente los totales a partir de los detalles.

    La resolución se indica siempre con `numero_resolucion` (el número que la
    DIAN le dio al emisor, lo único que este conoce); el id no se acepta. Los
    datos del `adquiriente` van anidados en cada documento y se guardan con él.
    """

    detalles = DocumentoDetalleSerializer(many=True)
    numero_resolucion = serializers.CharField(write_only=True)
    adquiriente = AdquirienteSerializer()
    # Todo lo que se referencia se busca solo dentro del alcance del
    # solicitante: un id de otra cuenta responde igual que uno inexistente.
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    documento_referencia = RelacionDelAlcance(
        queryset=models.Documento.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = models.Documento
        fields = [
            "id", "documento_tipo", "emisor", "numero_resolucion",
            "adquiriente", "prefijo", "consecutivo", "numero",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "total_descuentos", "total_cargos", "documento_referencia",
            "observaciones", "detalles",
        ]
        # Mensaje propio para la unicidad (emisor+prefijo+consecutivo+tipo) en vez
        # del genérico "deben formar un conjunto único".
        validators = [
            UniqueTogetherValidator(
                queryset=models.Documento.objects.all(),
                fields=["emisor", "prefijo", "consecutivo", "documento_tipo"],
                message="El documento ya fue creado.",
            )
        ]

    def get_fields(self):
        campos = super().get_fields()
        # En un PATCH solo se valida lo que llega: el documento ya tiene su
        # resolución y no hay por qué obligar a repetir el número.
        if self.partial:
            campos["numero_resolucion"].required = False
        return campos

    def validate_fecha_emision(self, fecha):
        """Solo se admite la fecha de hoy (regla FAD09).

        Vale igual para una fecha pasada que para una futura: lo que la DIAN
        compara es el día del ``IssueDate`` contra el del ``SigningTime``.
        """
        hoy = timezone.localdate()
        if fecha != hoy:
            raise serializers.ValidationError(mensaje_fecha_emision_no_es_hoy(hoy))
        return fecha

    def validate_detalles(self, detalles):
        if not detalles:
            raise serializers.ValidationError("El documento debe tener al menos un detalle.")
        return detalles

    def validate(self, attrs):
        """Resuelve la resolución y comprueba que el documento sea coherente.

        Traduce el `numero_resolucion` a la fila del emisor, exige que el
        documento referenciado sea de ese emisor —si no, una nota crédito podría
        colgar de la factura de una cuenta ajena—, que el número quepa en la
        resolución y que el emisor esté en condiciones de firmar.
        """
        # Una vez firmado, los datos del documento ya viajaron en el XML y en el
        # CUFE: cambiarlos aquí solo lograría que el PDF dejara de coincidir con
        # lo firmado.
        if self.instance is not None and not self.instance.es_borrador:
            raise serializers.ValidationError(MENSAJE_DOCUMENTO_NO_EDITABLE)

        emisor = attrs.get("emisor") or getattr(self.instance, "emisor", None)
        # No es un campo del modelo: sale de attrs siempre, o `create` reventaría.
        numero_resolucion = attrs.pop("numero_resolucion", None)
        if emisor is None:
            # Falta el emisor: ya lo reporta la validación de campo obligatorio.
            return attrs
        if numero_resolucion:
            attrs["resolucion"] = self._resolucion_por_numero(
                emisor, numero_resolucion, attrs,
            )
        # La resolución no se comprueba aquí: se buscó ya acotada a este emisor.
        # El adquiriente tampoco: sus datos llegan en la propia petición.
        referencia = attrs.get("documento_referencia")
        if referencia is not None and referencia.emisor_id != emisor.pk:
            raise serializers.ValidationError(
                {"documento_referencia": "No pertenece al emisor del documento."}
            )

        resolucion = attrs.get("resolucion") or getattr(self.instance, "resolucion", None)
        if resolucion is not None:
            errores = self._errores_de_numeracion(resolucion, attrs)
            if errores:
                raise serializers.ValidationError(errores)

        # Lo último, para no tapar un error de datos de la propia petición con
        # uno de configuración del emisor: sin certificado activo y vigente el
        # documento nacería muerto —se crearía bien y reventaría al firmarlo—,
        # así que se dice ya y no en `emitir/`.
        motivo = motivo_no_puede_emitir(emisor)
        if motivo:
            raise serializers.ValidationError({"emisor": motivo})
        return attrs

    def _errores_de_numeracion(self, resolucion, attrs):
        """Comprueba que el número del documento quepa en lo que autorizó la DIAN.

        El prefijo y el rango son parte de la resolución: numerar fuera de ellos
        produce un documento que la DIAN rechaza al enviarlo, así que se corta
        aquí y no cuando ya está firmado y consumió un consecutivo.
        """
        def dato(campo):
            if campo in attrs:
                return attrs[campo]
            return getattr(self.instance, campo, None)

        errores = {}
        if (dato("prefijo") or "") != resolucion.prefijo:
            errores["prefijo"] = mensaje_prefijo_ajeno(resolucion)

        consecutivo = dato("consecutivo")
        if consecutivo is not None and not (
            resolucion.rango_desde <= consecutivo <= resolucion.rango_hasta
        ):
            errores["consecutivo"] = mensaje_consecutivo_fuera_de_rango(resolucion)
        return errores

    def _resolucion_por_numero(self, emisor, numero, attrs):
        """Busca la resolución del emisor por su número DIAN.

        Se acota al emisor —que el campo `emisor` ya restringió al alcance del
        solicitante—, así que un número de otra cuenta responde igual que uno
        inexistente. Solo se consideran las activas: numerar con una dada de
        baja es justo lo que la bandera impide.
        """
        candidatas = list(ResolucionFacturacion.objects.filter(
            emisor=emisor, numero_resolucion=numero, activa=True,
        ))
        if not candidatas:
            raise serializers.ValidationError(
                {"numero_resolucion": MENSAJE_RESOLUCION_NO_ENCONTRADA}
            )
        if len(candidatas) > 1:
            # El número se repite si se importó para varios tipos de factura o
            # prefijos; el prefijo del documento es lo que desempata.
            candidatas = [r for r in candidatas if r.prefijo == (attrs.get("prefijo") or "")]
            if len(candidatas) != 1:
                raise serializers.ValidationError(
                    {"numero_resolucion": MENSAJE_RESOLUCION_AMBIGUA}
                )
        return candidatas[0]

    @transaction.atomic
    def create(self, validated_data):
        detalles_data = validated_data.pop("detalles")
        adquiriente_data = validated_data.pop("adquiriente")
        descuentos = validated_data.get("total_descuentos", Decimal("0")) or Decimal("0")
        cargos = validated_data.get("total_cargos", Decimal("0")) or Decimal("0")

        valor_bruto = Decimal("0")
        total_impuestos = Decimal("0")

        documento = models.Documento.objects.create(
            valor_bruto=Decimal("0"), total_impuestos=Decimal("0"),
            total_a_pagar=Decimal("0"), **validated_data,
        )

        responsabilidades = adquiriente_data.pop("responsabilidades", [])
        adquiriente = models.Adquiriente.objects.create(
            documento=documento, **adquiriente_data
        )
        adquiriente.responsabilidades.set(responsabilidades)

        for detalle_data in detalles_data:
            impuestos_data = detalle_data.pop("impuestos", [])
            detalle = models.DocumentoDetalle.objects.create(documento=documento, **detalle_data)
            valor_bruto += detalle.valor_total
            for imp in impuestos_data:
                impuesto = models.DocumentoDetalleImpuesto.objects.create(detalle=detalle, **imp)
                total_impuestos += impuesto.valor

        documento.valor_bruto = valor_bruto
        documento.total_impuestos = total_impuestos
        documento.total_a_pagar = valor_bruto - descuentos + cargos + total_impuestos
        documento.save(update_fields=["valor_bruto", "total_impuestos", "total_a_pagar"])
        return documento

    @transaction.atomic
    def update(self, instance, validated_data):
        """Permite corregir al receptor mientras el documento sea un borrador.

        El adquiriente no tiene endpoint propio, así que este es el único sitio
        donde se puede arreglar un dato suyo antes de emitir.
        """
        adquiriente_data = validated_data.pop("adquiriente", None)
        documento = super().update(instance, validated_data)
        if adquiriente_data is not None:
            responsabilidades = adquiriente_data.pop("responsabilidades", None)
            adquiriente = documento.adquiriente
            for campo, valor in adquiriente_data.items():
                setattr(adquiriente, campo, valor)
            adquiriente.save()
            if responsabilidades is not None:
                adquiriente.responsabilidades.set(responsabilidades)
        return documento

    def to_representation(self, instance):
        return DocumentoSerializer(instance, context=self.context).data
