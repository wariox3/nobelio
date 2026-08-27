"""Serializers de lectura y creación de la nómina electrónica."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from apps.emisores.models import Emisor
from apps.nomina import models
from apps.seguridad.alcance import RelacionDelAlcance

from .nomina_concepto import NominaConceptoSerializer

CERO = Decimal("0")

MENSAJE_NOMINA_NO_EDITABLE = (
    "La nómina ya fue firmada y no se puede modificar."
)
MENSAJE_AJUSTE_SIN_PREDECESORA = (
    "Una nota de ajuste debe indicar la nómina que ajusta: informe "
    "`nomina_predecesora`."
)
MENSAJE_AJUSTE_SIN_TIPO = (
    "Una nota de ajuste debe indicar `tipo_nota`: 1 para reemplazar el "
    "documento anterior, 2 para eliminarlo."
)
MENSAJE_TIPO_NOTA_SOLO_EN_AJUSTE = (
    "Solo la nota de ajuste lleva `tipo_nota` y `nomina_predecesora`."
)
MENSAJE_PREDECESORA_SIN_CUNE = (
    "La nómina que se ajusta todavía no tiene CUNE: emítala antes de ajustarla."
)


def mensaje_total_descuadrado(campo, esperado, recibido):
    """Mensaje para un total que no coincide con la suma de los conceptos.

    Se compara en vez de calcularse porque el total es lo que el ERP dice haber
    liquidado: si no cuadra con las líneas que manda, uno de los dos está mal y
    el que sabe cuál es él. Además entra en el CUNE, así que dejarlo pasar
    produciría un documento cuyo hash la DIAN no reproduce.
    """
    return (
        f"No cuadra con los conceptos: {campo} suma {esperado} y se informó "
        f"{recibido}."
    )


class NominaSerializer(serializers.ModelSerializer):
    """Serializer de lectura, con los conceptos anidados."""

    conceptos = NominaConceptoSerializer(many=True, read_only=True)
    errores = serializers.SerializerMethodField()
    empleado_nombre = serializers.CharField(
        source="empleado.nombre_completo", read_only=True,
    )
    estado_nombre = serializers.CharField(source="estado.nombre", read_only=True)

    class Meta:
        model = models.Nomina
        fields = [
            "id", "emisor", "empleado", "empleado_nombre",
            "estado", "estado_nombre", "tipo_xml", "tipo_nota",
            "nomina_predecesora",
            "prefijo", "consecutivo", "numero", "periodo_nomina",
            "fecha_liquidacion_inicio", "fecha_liquidacion_fin",
            "tiempo_laborado", "fecha_generacion", "hora_generacion",
            "fecha_pago", "moneda", "trm", "notas",
            "novedad", "cune_novedad",
            "total_devengados", "total_deducciones", "redondeo",
            "total_comprobante",
            "cune", "ambiente", "fecha_validacion", "errores", "conceptos",
            "creado_en", "actualizado_en",
        ]

    def get_errores(self, obj):
        return [
            {"regla": e.regla, "tipo": e.tipo, "mensaje": e.mensaje}
            for e in obj.errores.all()
        ]


class NominaListaSerializer(NominaSerializer):
    """Versión para el listado: sin los conceptos ni los errores."""

    conceptos = None
    errores = None

    class Meta(NominaSerializer.Meta):
        fields = [
            f for f in NominaSerializer.Meta.fields
            if f not in {"conceptos", "errores"}
        ]


class NominaCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con los conceptos anidados.

    Los totales llegan en la petición y **no se calculan**: se comprueban
    contra la suma de los conceptos y, si no cuadran, se rechaza la creación.
    """

    conceptos = NominaConceptoSerializer(many=True)
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    empleado = RelacionDelAlcance(
        queryset=models.Empleado.objects.all(), campo_emisor="emisor",
    )
    nomina_predecesora = RelacionDelAlcance(
        queryset=models.Nomina.objects.all(), required=False, allow_null=True,
    )

    class Meta:
        model = models.Nomina
        fields = [
            "id", "emisor", "empleado", "prefijo", "consecutivo", "numero",
            "tipo_xml", "tipo_nota", "nomina_predecesora",
            "periodo_nomina", "fecha_liquidacion_inicio", "fecha_liquidacion_fin",
            "tiempo_laborado", "fecha_generacion", "hora_generacion", "fecha_pago",
            "moneda", "trm", "notas", "novedad", "cune_novedad",
            "total_devengados", "total_deducciones", "redondeo",
            "total_comprobante", "conceptos",
        ]
        read_only_fields = ["numero"]
        validators = [
            UniqueTogetherValidator(
                queryset=models.Nomina.objects.all(),
                fields=["emisor", "prefijo", "consecutivo"],
                message="La nómina ya fue creada.",
            )
        ]

    def validate_conceptos(self, conceptos):
        if not conceptos:
            raise serializers.ValidationError(
                "La nómina debe tener al menos un concepto."
            )
        return conceptos

    def validate(self, attrs):
        # Firmada, sus datos ya viajaron en el XML y en el CUNE: cambiarlos aquí
        # solo lograría que la base dejara de coincidir con lo firmado.
        if self.instance is not None and not self.instance.es_borrador:
            raise serializers.ValidationError(MENSAJE_NOMINA_NO_EDITABLE)

        self._validar_periodo(attrs)
        self._validar_ajuste(attrs)
        self._validar_totales(attrs)
        return attrs

    def _dato(self, attrs, campo):
        if campo in attrs:
            return attrs[campo]
        return getattr(self.instance, campo, None)

    def _validar_periodo(self, attrs):
        inicio = self._dato(attrs, "fecha_liquidacion_inicio")
        fin = self._dato(attrs, "fecha_liquidacion_fin")
        if inicio and fin and fin < inicio:
            raise serializers.ValidationError(
                {"fecha_liquidacion_fin": "No puede ser anterior al inicio del periodo."}
            )

    def _validar_ajuste(self, attrs):
        """La nota de ajuste necesita a quién ajusta y cómo; la nómina, ninguna.

        El ``CUNEPred`` sale de la nómina ajustada, así que sin CUNE no hay nota
        que emitir: se corta al crear y no al firmar.
        """
        tipo_xml = self._dato(attrs, "tipo_xml")
        tipo_nota = self._dato(attrs, "tipo_nota")
        predecesora = self._dato(attrs, "nomina_predecesora")
        es_ajuste = tipo_xml == models.Nomina.TipoXML.AJUSTE

        if not es_ajuste:
            if tipo_nota or predecesora:
                raise serializers.ValidationError(MENSAJE_TIPO_NOTA_SOLO_EN_AJUSTE)
            return
        if not tipo_nota:
            raise serializers.ValidationError({"tipo_nota": MENSAJE_AJUSTE_SIN_TIPO})
        if predecesora is None:
            raise serializers.ValidationError(
                {"nomina_predecesora": MENSAJE_AJUSTE_SIN_PREDECESORA}
            )
        if not predecesora.cune:
            raise serializers.ValidationError(
                {"nomina_predecesora": MENSAJE_PREDECESORA_SIN_CUNE}
            )

    def _validar_totales(self, attrs):
        """Los totales informados tienen que cuadrar con los conceptos.

        Lo que aporta cada concepto es su ``valor`` más su parte no salarial:
        las dos se le pagan al trabajador y las dos cuentan en el total que la
        DIAN compara. Los conceptos sin pago (licencia no remunerada, huelga)
        traen valor cero y no suman.
        """
        conceptos = attrs.get("conceptos")
        if conceptos is None:
            return

        def sumar(grupo):
            return sum(
                (c.get("valor") or CERO) + (c.get("valor_no_salarial") or CERO)
                for c in conceptos
                if c.get("grupo") == grupo
            )

        errores = {}
        devengados = sumar(models.NominaConcepto.Grupo.DEVENGADO)
        deducciones = sumar(models.NominaConcepto.Grupo.DEDUCCION)
        informado_dev = self._dato(attrs, "total_devengados") or CERO
        informado_ded = self._dato(attrs, "total_deducciones") or CERO
        if devengados != informado_dev:
            errores["total_devengados"] = mensaje_total_descuadrado(
                "los devengados", devengados, informado_dev,
            )
        if deducciones != informado_ded:
            errores["total_deducciones"] = mensaje_total_descuadrado(
                "las deducciones", deducciones, informado_ded,
            )

        # El comprobante se comprueba contra los totales informados, no contra
        # las sumas: si aquellos ya están mal, el error que importa es el suyo.
        redondeo = self._dato(attrs, "redondeo") or CERO
        esperado = informado_dev - informado_ded + redondeo
        informado_total = self._dato(attrs, "total_comprobante") or CERO
        if not errores and esperado != informado_total:
            errores["total_comprobante"] = (
                f"No cuadra: devengados menos deducciones más redondeo da "
                f"{esperado} y se informó {informado_total}."
            )
        if errores:
            raise serializers.ValidationError(errores)

    @transaction.atomic
    def create(self, validated_data):
        conceptos = validated_data.pop("conceptos")
        nomina = models.Nomina.objects.create(**validated_data)
        models.NominaConcepto.objects.bulk_create([
            models.NominaConcepto(nomina=nomina, **datos) for datos in conceptos
        ])
        return nomina

    @transaction.atomic
    def update(self, instance, validated_data):
        conceptos = validated_data.pop("conceptos", None)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        if conceptos is not None:
            instance.conceptos.all().delete()
            models.NominaConcepto.objects.bulk_create([
                models.NominaConcepto(nomina=instance, **datos) for datos in conceptos
            ])
        return instance
