"""Serializers de lectura y creación del documento electrónico."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from apps.documentos import models
from apps.emisores.models import Emisor, Resolucion
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


# Factura y documento soporte. La lista vive en el modelo porque
# `generar_y_firmar` exige lo mismo al emitir.
TIPOS_CON_RESOLUCION = models.DocumentoTipo.CODIGOS_CON_RESOLUCION


def mensaje_sin_resolucion(tipo):
    """Mensaje para un documento que se numera con resolución y no la indica."""
    return (
        f"{tipo.nombre} debe indicar el número de una resolución activa del emisor."
    )

# Tipos cuyo XML se construye con DiscrepancyResponse y BillingReference: sin
# el documento corregido no hay nota que valga (ver `_ConstructorNotaUBL`). La
# lista vive en el modelo porque `generar_y_firmar` exige lo mismo al emitir.
TIPOS_QUE_EXIGEN_REFERENCIA = models.DocumentoTipo.CODIGOS_CON_REFERENCIA


# Lista de conceptos de corrección (ResponseCode) aplicable a cada tipo de nota.
CONCEPTOS_POR_TIPO = {
    models.DocumentoTipo.Codigo.NOTA_CREDITO: models.Documento.ConceptoNotaCredito,
    models.DocumentoTipo.Codigo.NOTA_DEBITO: models.Documento.ConceptoNotaDebito,
    # La nota de ajuste tiene su propia lista (ConceptoNotaAjuste del anexo DS):
    # los códigos coinciden en número con los de la nota crédito pero no en
    # redacción, y es la que la DIAN valida en el ResponseCode.
    models.DocumentoTipo.Codigo.NOTA_AJUSTE: models.Documento.ConceptoNotaAjuste,
}


def mensaje_concepto_invalido(tipo, conceptos):
    """Mensaje que enumera los conceptos válidos para ese tipo de nota."""
    opciones = ", ".join(f"{c.value} ({c.label})" for c in conceptos)
    return (
        f"Indique por qué se corrige el documento. Para una {tipo.nombre.lower()} "
        f"los conceptos son: {opciones}."
    )


MENSAJE_CONCEPTO_SOLO_EN_NOTAS = (
    "Solo las notas —crédito, débito y de ajuste— llevan concepto de corrección."
)

# Forma de pago a crédito (lista FormasPago de la DIAN): 1 contado, 2 crédito.
CODIGO_FORMA_PAGO_CREDITO = "2"

MENSAJE_CREDITO_SIN_VENCIMIENTO = (
    "Una venta a crédito debe indicar hasta cuándo hay plazo para pagar: "
    "informe la fecha de vencimiento."
)
MENSAJE_VENCIMIENTO_ANTERIOR_A_EMISION = (
    "La fecha de vencimiento no puede ser anterior a la de emisión."
)


def mensaje_nota_sin_referencia(tipo):
    """Mensaje para una nota que no indica el documento que corrige."""
    return (
        f"Una {tipo.nombre.lower()} debe referenciar el documento que corrige: "
        "indique el id de la factura en `documento_referencia`."
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


# País del vendedor que hace residente al documento soporte: con él el
# CustomizationID sale `10` (ver `customization_id_default` en `apps/dian/ubl.py`)
# y el anexo pasa a exigir la dirección física del vendedor.
CODIGO_PAIS_COLOMBIA = "CO"

# Campos del vendedor que el documento soporte con CustomizationID=10 necesita
# para armar el `cac:PhysicalLocation/cac:Address`, con el nombre que el emisor
# reconoce.
CAMPOS_DIRECCION_VENDEDOR = (
    ("municipio", "municipio"),
    ("direccion", "dirección"),
    ("codigo_postal", "código postal"),
)


def mensaje_vendedor_sin_direccion(faltantes):
    """Mensaje para el documento soporte cuyo vendedor residente no trae dirección.

    Se corta al crear y no al emitir: la DIAN rechaza el documento (regla
    DSAJ08a) cuando el grupo de la dirección no está completo, y para entonces
    el consecutivo ya está reservado y el documento firmado no se puede editar.
    """
    return (
        "Un documento soporte (o su nota de ajuste) con vendedor residente en "
        "Colombia debe informar la dirección del vendedor; falta: "
        f"{', '.join(faltantes)}."
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
            "envio", "ambiente", "fecha_validacion", "notificado", "errores",
            "concepto_correccion", "fecha_vencimiento",
            "orden_compra", "orden_compra_fecha", "orden_compra_tipo",
            "orden_compra_documento",
            "fecha_emision", "hora_emision", "moneda", "forma_pago", "medio_pago",
            "valor_bruto", "total_impuestos",
            "total_descuentos", "descuentos_motivo", "total_cargos", "cargos_motivo",
            "total_a_pagar", "documento_referencia", "observaciones", "detalles",
            "creado_en", "actualizado_en",
        ]
        read_only_fields = [
            "estado", "cufe_cude", "track_id", "envio", "ambiente", "fecha_validacion",
            "notificado",
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
    # Opcional porque solo la factura se numera con resolución: la nota lleva
    # su propia numeración y su XML ni siquiera incluye el InvoiceControl.
    numero_resolucion = serializers.CharField(write_only=True, required=False)
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
            "total_descuentos", "descuentos_motivo",
            "total_cargos", "cargos_motivo", "documento_referencia",
            "concepto_correccion", "fecha_vencimiento",
            "orden_compra", "orden_compra_fecha", "orden_compra_tipo",
            "orden_compra_documento",
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
        tipo = attrs.get("documento_tipo") or getattr(self.instance, "documento_tipo", None)
        if numero_resolucion:
            attrs["resolucion"] = self._resolucion_por_numero(
                emisor, numero_resolucion, attrs, tipo,
            )
        # La resolución no se comprueba aquí: se buscó ya acotada a este emisor.
        # El adquiriente tampoco: sus datos llegan en la propia petición.
        referencia = attrs.get("documento_referencia")
        if referencia is None and self.instance is not None:
            referencia = self.instance.documento_referencia
        if referencia is not None and referencia.emisor_id != emisor.pk:
            raise serializers.ValidationError(
                {"documento_referencia": "No pertenece al emisor del documento."}
            )
        # Una nota sin referencia es un documento que nace muerto: se crea bien,
        # consume un consecutivo y solo falla al emitirlo, cuando ya no se puede
        # editar. La misma regla la exige `generar_y_firmar`.
        if (
            referencia is None
            and tipo is not None
            and tipo.codigo in TIPOS_QUE_EXIGEN_REFERENCIA
        ):
            raise serializers.ValidationError(
                {"documento_referencia": mensaje_nota_sin_referencia(tipo)}
            )
        self._validar_concepto(attrs, tipo)
        self._validar_vencimiento(attrs)
        self._validar_direccion_vendedor(attrs, tipo)

        resolucion = attrs.get("resolucion") or getattr(self.instance, "resolucion", None)
        # Sin resolución no hay sts:InvoiceControl que emitir —ni clave técnica
        # con la que calcular el CUFE en la factura—. Lo mismo exige
        # `generar_y_firmar`.
        if resolucion is None and tipo is not None and tipo.codigo in TIPOS_CON_RESOLUCION:
            raise serializers.ValidationError(
                {"numero_resolucion": mensaje_sin_resolucion(tipo)}
            )
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

    def _validar_concepto(self, attrs, tipo):
        """El concepto de corrección va en las notas, y con su propia lista.

        Se exige en vez de asumir uno: el ``ResponseCode`` es lo que le dice a
        la DIAN si la nota anula la factura o solo devuelve parte, y ninguna
        suposición nuestra puede acertar eso por el emisor.
        """
        if tipo is None:
            return
        concepto = attrs.get("concepto_correccion")
        if concepto is None and self.instance is not None:
            concepto = self.instance.concepto_correccion
        conceptos = CONCEPTOS_POR_TIPO.get(tipo.codigo)
        if conceptos is None:
            if concepto:
                raise serializers.ValidationError(
                    {"concepto_correccion": MENSAJE_CONCEPTO_SOLO_EN_NOTAS}
                )
            return
        if concepto not in conceptos.values:
            raise serializers.ValidationError(
                {"concepto_correccion": mensaje_concepto_invalido(tipo, conceptos)}
            )

    def _validar_direccion_vendedor(self, attrs, tipo):
        """El vendedor de un documento soporte residente lleva dirección física.

        Vale igual para su nota de ajuste, que repite las mismas partes.
        En el documento soporte el `adquiriente` no es el receptor sino el
        vendedor (el sujeto no obligado), y su país es lo que decide el
        `CustomizationID`: residente en Colombia -> `10`, y entonces el anexo
        exige el bloque `cac:PhysicalLocation`. Al no residente (`11`) no se le
        pide: se identifica por la lista TipoIdFiscal y no lleva ese bloque.
        """
        if tipo is None or tipo.codigo not in models.DocumentoTipo.CODIGOS_CON_VENDEDOR_NO_OBLIGADO:
            return
        adquiriente = attrs.get("adquiriente")
        if adquiriente is None and self.instance is not None:
            adquiriente = self.instance.adquiriente
        if adquiriente is None:
            return

        def dato(campo):
            # Al crear llega el dict validado del serializer anidado; al editar,
            # la instancia que ya está guardada.
            if isinstance(adquiriente, dict):
                return adquiriente.get(campo)
            return getattr(adquiriente, campo, None)

        pais = dato("pais")
        if pais is None or pais.codigo != CODIGO_PAIS_COLOMBIA:
            return
        faltantes = [
            etiqueta for campo, etiqueta in CAMPOS_DIRECCION_VENDEDOR if not dato(campo)
        ]
        if faltantes:
            raise serializers.ValidationError(
                {"adquiriente": mensaje_vendedor_sin_direccion(faltantes)}
            )

    def _validar_vencimiento(self, attrs):
        """El plazo de pago: obligatorio a crédito, coherente siempre.

        La DIAN exige el ``DueDate`` cuando la venta es a crédito, y sin campo
        que lo lleve el documento saldría sin él y lo rechazarían al enviarlo.
        """
        def dato(campo):
            if campo in attrs:
                return attrs[campo]
            return getattr(self.instance, campo, None)

        vencimiento = dato("fecha_vencimiento")
        forma_pago = dato("forma_pago")
        es_credito = forma_pago is not None and forma_pago.codigo == CODIGO_FORMA_PAGO_CREDITO
        if es_credito and vencimiento is None:
            raise serializers.ValidationError(
                {"fecha_vencimiento": MENSAJE_CREDITO_SIN_VENCIMIENTO}
            )
        emision = dato("fecha_emision")
        if vencimiento is not None and emision is not None and vencimiento < emision:
            raise serializers.ValidationError(
                {"fecha_vencimiento": MENSAJE_VENCIMIENTO_ANTERIOR_A_EMISION}
            )

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

    def _resolucion_por_numero(self, emisor, numero, attrs, tipo):
        """Busca la resolución del emisor por su número DIAN.

        Se acota al emisor —que el campo `emisor` ya restringió al alcance del
        solicitante—, así que un número de otra cuenta responde igual que uno
        inexistente. Solo se consideran las activas: numerar con una dada de
        baja es justo lo que la bandera impide.
        """
        candidatas = list(
            Resolucion.objects
            .filter(emisor=emisor, numero_resolucion=numero, activa=True)
            .select_related("tipo_factura")
        )
        if not candidatas:
            raise serializers.ValidationError(
                {"numero_resolucion": MENSAJE_RESOLUCION_NO_ENCONTRADA}
            )
        # El número se repite si se importó para varios tipos de documento o
        # prefijos. Desempata primero el tipo del documento —el `codigo_dian`
        # del tipo es el código del catálogo con el que se guardó la
        # resolución—, y si eso no basta, el prefijo.
        if len(candidatas) > 1 and tipo is not None and tipo.codigo_dian:
            del_tipo = [r for r in candidatas if r.tipo_factura.codigo == tipo.codigo_dian]
            # Si ninguna es de ese tipo no se descarta nada: el emisor puede
            # haber registrado su numeración bajo un solo tipo.
            if del_tipo:
                candidatas = del_tipo
        if len(candidatas) > 1:
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

        # En el documento soporte las retenciones no suman al total a pagar: el
        # adquiriente las practica sobre el pago, no se las cobra el vendedor, y
        # en el XML van fuera del TaxInclusiveAmount.
        retenciones_aparte = (
            documento.documento_tipo.codigo
            in models.DocumentoTipo.CODIGOS_CON_RETENCIONES
        )

        for detalle_data in detalles_data:
            impuestos_data = detalle_data.pop("impuestos", [])
            detalle = models.DocumentoDetalle.objects.create(documento=documento, **detalle_data)
            valor_bruto += detalle.valor_total
            for imp in impuestos_data:
                impuesto = models.DocumentoDetalleImpuesto.objects.create(detalle=detalle, **imp)
                if retenciones_aparte and impuesto.tributo.es_retencion:
                    continue
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
