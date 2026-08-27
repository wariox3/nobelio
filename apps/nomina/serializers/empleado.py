"""Serializer del empleado."""
from rest_framework import serializers

from apps.emisores.models import Emisor
from apps.nomina.models import Empleado
from apps.seguridad.alcance import RelacionDelAlcance


class EmpleadoSerializer(serializers.ModelSerializer):
    """Trabajador de un emisor, con su contrato y su forma de cobro.

    Tiene endpoint propio, a diferencia del adquiriente de la factura: el
    empleado no es un dato de un documento sino una relación que dura, y cada
    nómina lo referencia por id.
    """

    # El emisor se busca solo dentro del alcance del solicitante: uno de otra
    # cuenta responde igual que uno inexistente.
    emisor = RelacionDelAlcance(queryset=Emisor.objects.all(), campo_emisor="id")
    nombre_completo = serializers.CharField(read_only=True)
    # Se calcula desde el retiro que reporten sus nóminas: informarlo no
    # tendría efecto.
    activo = serializers.BooleanField(read_only=True)
    tipo_identificacion_codigo = serializers.CharField(
        source="tipo_identificacion.codigo", read_only=True,
    )

    class Meta:
        model = Empleado
        fields = [
            "id", "emisor",
            "tipo_identificacion", "tipo_identificacion_codigo",
            "numero_documento", "primer_apellido", "segundo_apellido",
            "primer_nombre", "otros_nombres", "nombre_completo",
            "codigo_trabajador", "tipo_trabajador", "subtipo_trabajador",
            "tipo_contrato", "alto_riesgo_pension", "salario_integral",
            "sueldo", "fecha_ingreso",
            "pais", "departamento", "municipio", "direccion",
            "forma_pago", "medio_pago", "banco", "tipo_cuenta", "numero_cuenta",
            "activo", "creado_en", "actualizado_en",
        ]
        read_only_fields = ["creado_en", "actualizado_en"]


class EmpleadoAnidadoSerializer(EmpleadoSerializer):
    """El empleado tal como viaja dentro de la nómina.

    Igual que el de su endpoint pero sin ``emisor`` —lo pone la nómina, que ya
    lo trae— y sin el validador de unicidad, porque aquí el par emisor +
    identificación no identifica un error sino al empleado que hay que crear o
    actualizar: la nómina hace ese *upsert* al guardarse.
    """

    class Meta(EmpleadoSerializer.Meta):
        fields = [f for f in EmpleadoSerializer.Meta.fields if f != "emisor"]
        validators = []
