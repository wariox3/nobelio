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
            "sueldo", "fecha_ingreso", "fecha_retiro",
            "pais", "departamento", "municipio", "direccion",
            "forma_pago", "medio_pago", "banco", "tipo_cuenta", "numero_cuenta",
            "activo", "creado_en", "actualizado_en",
        ]
        read_only_fields = ["creado_en", "actualizado_en"]

    def validate(self, attrs):
        """El retiro no puede ser anterior al ingreso.

        Las dos fechas viajan en el ``Periodo`` del XML y la DIAN las compara;
        un par invertido es un rechazo con el consecutivo ya gastado.
        """
        def dato(campo):
            if campo in attrs:
                return attrs[campo]
            return getattr(self.instance, campo, None)

        ingreso, retiro = dato("fecha_ingreso"), dato("fecha_retiro")
        if ingreso and retiro and retiro < ingreso:
            raise serializers.ValidationError(
                {"fecha_retiro": "No puede ser anterior a la fecha de ingreso."}
            )
        return attrs
