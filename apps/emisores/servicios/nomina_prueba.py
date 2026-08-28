"""Nómina de prueba para la habilitación de nómina electrónica."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.catalogos.models import (
    FormaPago,
    MedioPago,
    Moneda,
    PeriodoNomina,
    SubTipoTrabajador,
    TipoContrato,
    TipoTrabajador,
)
from apps.nomina.models import Empleado, Nomina, NominaConcepto

PREFIJO_POR_DEFECTO = "NESETP"

# Importes de la nómina de prueba: un básico redondo con la salud y la pensión
# de ley, para que los totales cuadren a la vista.
SUELDO = Decimal("1000000.00")
PORCENTAJE_APORTE = Decimal("4.00")
APORTE = Decimal("40000.00")


def _siguiente_consecutivo(emisor, prefijo) -> int:
    """El siguiente número libre del emisor para ese prefijo.

    El endpoint se llama varias veces mientras se prueba la habilitación, y con
    un consecutivo fijo la segunda llamada chocaría con la restricción de
    unicidad.
    """
    ultimo = Nomina.objects.filter(emisor=emisor, prefijo=prefijo).aggregate(
        ultimo=Max("consecutivo"),
    )["ultimo"]
    return (ultimo or 0) + 1


@transaction.atomic
def crear_nomina_prueba(emisor, *, prefijo=None, consecutivo=None,
                        periodo_inicio=None, periodo_fin=None) -> Nomina:
    """Crea —solo crea— una nómina en borrador para el emisor.

    No la firma ni la envía: es material para probar la emisión de nómina
    electrónica, igual que ``crear_factura_prueba`` lo es para la facturación.

    El trabajador se toma del propio emisor, como allí se toma el adquiriente:
    así el documento sale con una identificación real y registrada, sin
    inventar una persona.

    El **periodo de liquidación** se puede fijar, y hace falta para llenar el
    Set de Pruebas: la DIAN rechaza con la regla 90 ("documento procesado
    anteriormente") una segunda nómina del mismo trabajador para el mismo
    periodo, porque a nadie se le paga dos veces el mismo mes. Sin fechas, sale
    el mes en curso, que es lo útil para una prueba suelta pero repetido diez
    veces son nueve rechazos.
    """
    hoy = timezone.localdate()
    prefijo = PREFIJO_POR_DEFECTO if prefijo is None else prefijo
    if consecutivo is None:
        consecutivo = _siguiente_consecutivo(emisor, prefijo)

    fin = periodo_fin or hoy
    inicio = periodo_inicio or fin.replace(day=1)
    if inicio > fin:
        raise ValueError(
            "El periodo de liquidación empieza después de terminar: "
            f"{inicio} -> {fin}."
        )
    # El ingreso tiene que ser anterior al periodo que se liquida, o el propio
    # documento se contradice.
    ingreso = min(inicio, hoy).replace(month=1, day=1)

    empleado, _ = Empleado.objects.update_or_create(
        emisor=emisor,
        tipo_identificacion=emisor.tipo_identificacion,
        numero_documento=emisor.numero_identificacion,
        defaults={
            "primer_nombre": "Empleado",
            "primer_apellido": "De Prueba",
            "codigo_trabajador": "PRUEBA-1",
            "sueldo": SUELDO,
            "fecha_ingreso": ingreso,
            "tipo_trabajador": TipoTrabajador.objects.get(codigo="01"),
            "subtipo_trabajador": SubTipoTrabajador.objects.get(codigo="00"),
            "tipo_contrato": TipoContrato.objects.get(codigo="2"),
            "pais": emisor.pais,
            "departamento": emisor.departamento,
            "municipio": emisor.municipio,
            "direccion": emisor.direccion,
            "forma_pago": FormaPago.objects.get(codigo="1"),
            "medio_pago": MedioPago.objects.get(codigo="10"),
        },
    )

    nomina = Nomina.objects.create(
        emisor=emisor,
        empleado=empleado,
        ambiente=Nomina.Ambiente.PRUEBAS,
        prefijo=prefijo,
        consecutivo=consecutivo,
        periodo_nomina=PeriodoNomina.objects.get(codigo="5"),
        moneda=Moneda.objects.get(codigo="COP"),
        fecha_liquidacion_inicio=inicio,
        fecha_liquidacion_fin=fin,
        tiempo_laborado=(fin - inicio).days + 1,
        fecha_generacion=hoy,
        hora_generacion=timezone.localtime().time(),
        fecha_pago=fin,
        notas="Nómina de prueba (habilitación).",
        # Las condiciones se copian del empleado, como haría el serializer.
        codigo_trabajador=empleado.codigo_trabajador,
        sueldo=empleado.sueldo,
        tipo_trabajador=empleado.tipo_trabajador,
        subtipo_trabajador=empleado.subtipo_trabajador,
        tipo_contrato=empleado.tipo_contrato,
        lugar_trabajo_pais=empleado.pais,
        lugar_trabajo_departamento=empleado.departamento,
        lugar_trabajo_municipio=empleado.municipio,
        lugar_trabajo_direccion=empleado.direccion,
        forma_pago=empleado.forma_pago,
        medio_pago=empleado.medio_pago,
        total_devengados=SUELDO,
        total_deducciones=APORTE * 2,
        total_comprobante=SUELDO - APORTE * 2,
    )

    NominaConcepto.objects.bulk_create([
        NominaConcepto(
            nomina=nomina,
            grupo=NominaConcepto.Grupo.DEVENGADO,
            concepto=NominaConcepto.Concepto.BASICO,
            cantidad=nomina.tiempo_laborado,
            valor=SUELDO,
        ),
        NominaConcepto(
            nomina=nomina,
            grupo=NominaConcepto.Grupo.DEDUCCION,
            concepto=NominaConcepto.Concepto.SALUD,
            porcentaje=PORCENTAJE_APORTE,
            valor=APORTE,
        ),
        NominaConcepto(
            nomina=nomina,
            grupo=NominaConcepto.Grupo.DEDUCCION,
            concepto=NominaConcepto.Concepto.FONDO_PENSION,
            porcentaje=PORCENTAJE_APORTE,
            valor=APORTE,
        ),
    ])
    return nomina
