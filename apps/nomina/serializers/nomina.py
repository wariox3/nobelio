"""Serializers de lectura y creación de la nómina electrónica."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from apps.emisores.models import Emisor
from apps.nomina import models
from apps.seguridad.alcance import RelacionDelAlcance

from .empleado import EmpleadoAnidadoSerializer
from .nomina_concepto import NominaConceptoSerializer

CERO = Decimal("0")

# Condiciones del trabajador que el documento congela al crearse. Si no vienen
# en la petición se copian del empleado, que es donde están las vigentes: así el
# ERP solo manda lo que cambió ese mes y no repite trece campos cada vez.
CONDICIONES_DEL_EMPLEADO = {
    "codigo_trabajador": "codigo_trabajador",
    "alto_riesgo_pension": "alto_riesgo_pension",
    "salario_integral": "salario_integral",
    "sueldo": "sueldo",
    "tipo_trabajador": "tipo_trabajador",
    "subtipo_trabajador": "subtipo_trabajador",
    "tipo_contrato": "tipo_contrato",
    "lugar_trabajo_pais": "pais",
    "lugar_trabajo_departamento": "departamento",
    "lugar_trabajo_municipio": "municipio",
    "lugar_trabajo_direccion": "direccion",
    "forma_pago": "forma_pago",
    "medio_pago": "medio_pago",
    "banco": "banco",
    "tipo_cuenta": "tipo_cuenta",
    "numero_cuenta": "numero_cuenta",
}

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
            "cune", "envio", "ambiente", "fecha_validacion", "errores", "conceptos",
            # Las condiciones con las que se emitió, congeladas al crear.
            *CONDICIONES_DEL_EMPLEADO, "fecha_retiro",
            "creado_en", "actualizado_en",
        ]

    def get_errores(self, obj):
        return [
            {"regla": e.regla, "tipo": e.tipo, "mensaje": e.mensaje}
            for e in obj.errores.all()
        ]


class NominaListaSerializer(NominaSerializer):
    """Versión para el listado: sin los conceptos y con los errores contados.

    Mismo criterio que el listado de documentos: para pintar la lista basta
    saber cuántos rechazos tiene, y el detalle es quien los explica. El ViewSet
    lo resuelve anotando un ``COUNT``, así que no se traen las filas de error.
    """

    conceptos = None
    errores = None
    total_errores = serializers.SerializerMethodField()

    class Meta(NominaSerializer.Meta):
        fields = [
            *[
                f for f in NominaSerializer.Meta.fields
                if f not in {"conceptos", "errores"}
            ],
            "total_errores",
        ]

    def get_total_errores(self, obj):
        """Usa la anotación del ViewSet; si no está, cuenta a mano."""
        anotado = getattr(obj, "total_errores", None)
        return anotado if anotado is not None else obj.errores.count()


class NominaCrearSerializer(serializers.ModelSerializer):
    """Serializer de creación con los conceptos anidados.

    Los totales llegan en la petición y **no se calculan**: se comprueban
    contra la suma de los conceptos y, si no cuadran, se rechaza la creación.
    """

    conceptos = NominaConceptoSerializer(many=True)
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    # Anidado, como el adquiriente de la factura: el ERP manda al trabajador en
    # la misma petición y no tiene que conocer nuestros ids ni dar de alta al
    # empleado aparte. La diferencia con el adquiriente es que aquí sí hay
    # maestro: la fila se busca por emisor + identificación y se actualiza, en
    # vez de crear una nueva por documento.
    empleado = EmpleadoAnidadoSerializer()
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
            *CONDICIONES_DEL_EMPLEADO,
            # No se hereda de nadie: el maestro ya no guarda el retiro, así que
            # llega solo cuando el trabajador se va y es un dato del documento.
            "fecha_retiro",
        ]
        read_only_fields = ["numero"]
        # Todas opcionales: se heredan del empleado si no vienen.
        extra_kwargs = {
            campo: {"required": False} for campo in CONDICIONES_DEL_EMPLEADO
        }
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

        self._heredar_condiciones(attrs)
        self._validar_periodo(attrs)
        self._validar_retiro(attrs)
        self._validar_ajuste(attrs)
        self._validar_totales(attrs)
        return attrs

    def _heredar_condiciones(self, attrs):
        """Rellena con los datos del trabajador las condiciones que no vengan.

        La fuente es lo que trae la petición y, para lo que no traiga, la fila
        del maestro que ya exista. Así el ERP puede mandar el bloque completo
        del empleado, mandar solo lo que cambió, o poner la condición suelta en
        la nómina cuando quiera que valga para ese periodo y no se guarde como
        vigente.

        Solo al crear: en una edición, un campo ausente es "no lo toques", no
        "vuelve a copiarlo" —que pisaría lo corregido a mano en el borrador—.
        """
        if self.instance is not None:
            return
        datos = attrs.get("empleado") or {}
        existente = self._empleado_existente(attrs)
        for campo, origen in CONDICIONES_DEL_EMPLEADO.items():
            if origen in datos:
                attrs.setdefault(campo, datos[origen])
            elif existente is not None:
                attrs.setdefault(campo, getattr(existente, origen))

    def _empleado_existente(self, attrs):
        """La fila del maestro que corresponde al trabajador de la petición."""
        datos = attrs.get("empleado") or {}
        emisor = attrs.get("emisor") or getattr(self.instance, "emisor", None)
        if not (emisor and datos.get("numero_documento")):
            return None
        return models.Empleado.objects.filter(
            emisor=emisor,
            tipo_identificacion=datos.get("tipo_identificacion"),
            numero_documento=datos["numero_documento"],
        ).first()

    def _guardar_empleado(self, emisor, datos):
        """Crea o actualiza el empleado del emisor y lo devuelve.

        La clave es emisor + tipo y número de identificación, la misma que
        impone la restricción del modelo. Lo que llegue en la petición pasa a
        ser lo vigente en el maestro: el documento ya se quedó con su copia, así
        que actualizarlo no reescribe nada de lo emitido.
        """
        identificacion = {
            "tipo_identificacion": datos.pop("tipo_identificacion"),
            "numero_documento": datos.pop("numero_documento"),
        }
        empleado, _ = models.Empleado.objects.update_or_create(
            emisor=emisor, **identificacion, defaults=datos,
        )
        return empleado

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

    def _validar_retiro(self, attrs):
        """El retiro no puede ser anterior al ingreso del trabajador.

        Las dos fechas van juntas en el ``Periodo`` del XML y la DIAN las
        compara; un par invertido es un rechazo con el consecutivo ya gastado.
        El retiro es del documento y el ingreso del maestro, así que la
        comprobación cruza los dos.
        """
        retiro = self._dato(attrs, "fecha_retiro")
        if retiro is None:
            return
        datos = attrs.get("empleado") or {}
        ingreso = datos.get("fecha_ingreso")
        if ingreso is None:
            existente = self._empleado_existente(attrs)
            if existente is None:
                existente = getattr(self.instance, "empleado", None)
            ingreso = getattr(existente, "fecha_ingreso", None)
        if ingreso and retiro < ingreso:
            raise serializers.ValidationError(
                {"fecha_retiro": "No puede ser anterior a la fecha de ingreso."}
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
        empleado = self._guardar_empleado(
            validated_data["emisor"], validated_data.pop("empleado"),
        )
        nomina = models.Nomina.objects.create(empleado=empleado, **validated_data)
        models.NominaConcepto.objects.bulk_create([
            models.NominaConcepto(nomina=nomina, **datos) for datos in conceptos
        ])
        return nomina

    @transaction.atomic
    def update(self, instance, validated_data):
        conceptos = validated_data.pop("conceptos", None)
        if "empleado" in validated_data:
            validated_data["empleado"] = self._guardar_empleado(
                validated_data.get("emisor") or instance.emisor,
                validated_data.pop("empleado"),
            )
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        if conceptos is not None:
            instance.conceptos.all().delete()
            models.NominaConcepto.objects.bulk_create([
                models.NominaConcepto(nomina=instance, **datos) for datos in conceptos
            ])
        return instance
