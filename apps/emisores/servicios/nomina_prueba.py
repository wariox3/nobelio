"""Nóminas de prueba para la habilitación de nómina electrónica."""
import calendar
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db import IntegrityError
from django.db.models import Max, Min
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
from apps.nucleo.models import Ambiente

from .emision import motivo_no_puede_emitir

PREFIJO_POR_DEFECTO = "NESETP"

# Cuántas nóminas deja el alta del software de nómina.
NOMINAS_DE_PRUEBA = 10

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
    electrónica, igual que ``crear_facturas_de_prueba`` lo es para la facturación.

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


def _mes(hoy, atras):
    """Primer y último día del mes que cae ``atras`` meses antes de ``hoy``."""
    total = hoy.year * 12 + (hoy.month - 1) - atras
    anio, indice = divmod(total, 12)
    mes = indice + 1
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def crear_nominas_de_prueba(emisor, cantidad=NOMINAS_DE_PRUEBA):
    """Deja ``cantidad`` nóminas de prueba en borrador. Devuelve la lista.

    Es lo que siembra el alta del software de nómina, igual que
    ``crear_facturas_de_prueba`` en facturación.

    **Una por mes, hacia atrás desde el mes en curso**, y ese detalle es el
    motivo de que esto exista en vez de llamar diez veces a
    ``crear_nomina_prueba``: la DIAN rechaza con la regla 90 una segunda nómina
    del mismo trabajador para el mismo periodo, porque a nadie se le paga dos
    veces el mismo mes. Diez con el periodo por defecto serían nueve rechazos.

    Salen en orden cronológico, así que el consecutivo más bajo es el mes más
    antiguo.

    **No duplica.** Si el emisor ya tiene nóminas con este prefijo se devuelve
    la lista vacía: el caso llega solo, porque dar de baja el software y volver
    a registrarlo pasa por aquí otra vez.
    """
    if Nomina.objects.filter(emisor=emisor, prefijo=PREFIJO_POR_DEFECTO).exists():
        return []

    hoy = timezone.localdate()
    with transaction.atomic():
        return [
            crear_nomina_prueba(
                emisor,
                periodo_inicio=inicio,
                periodo_fin=fin,
            )
            for inicio, fin in (
                _mes(hoy, atras) for atras in range(cantidad - 1, -1, -1)
            )
        ]


def siguiente_periodo(emisor, prefijo=PREFIJO_POR_DEFECTO):
    """El mes que continúa la serie de prueba, hacia atrás.

    Las diez del alta cubren los diez meses hasta el actual, así que la
    siguiente suelta va **antes** de la más antigua: hacia adelante caería en el
    futuro, y un periodo de liquidación que aún no ha pasado es un documento que
    se contradice.

    No es cosmético. La DIAN rechaza con la regla 90 una segunda nómina del
    mismo trabajador para el mismo periodo, y el trabajador de todas estas es el
    mismo, así que repetir mes es repetir rechazo.
    """
    mas_antigua = Nomina.objects.filter(
        emisor=emisor, prefijo=prefijo,
    ).aggregate(inicio=Min("fecha_liquidacion_inicio"))["inicio"]
    if mas_antigua is None:
        return _mes(timezone.localdate(), 0)
    return _mes(mas_antigua, 1)


def crear_nomina_de_prueba(software, consecutivo=None):
    """Una nómina de prueba en borrador para ese software de nómina.

    La misma que siembra el alta, pero de una en una. Sin ``consecutivo`` toma
    el siguiente libre del emisor para el prefijo de pruebas, y el periodo
    continúa la serie (ver ``siguiente_periodo``); si hace falta otro, se ajusta
    en el borrador con un ``PATCH``.

    Lanza ``ValueError`` con el motivo cuando no se puede: quien llama lo
    traduce a un 400.
    """
    from apps.emisores.models import SoftwareDian

    if software.tipo != SoftwareDian.Tipo.NOMINA:
        etiqueta = SoftwareDian.Tipo(software.tipo).label.lower()
        raise ValueError(
            f"Este software es de {etiqueta}, no de nómina electrónica. Los "
            f"documentos de prueba de facturación se crean desde su resolución "
            f"(POST /api/emisores/resolucion/{{id}}/crear-documento-prueba/)."
        )

    emisor = software.emisor
    if emisor.ambiente_nomina != Ambiente.PRUEBAS:
        # La nómina sale sellada como de pruebas —lo fija `crear_nomina_prueba`—
        # pero gastaría un consecutivo de la numeración real de nómina.
        raise ValueError(
            f"El emisor {emisor.razon_social} ya está en producción para "
            f"nómina. Una nómina de prueba consumiría un consecutivo de su "
            f"numeración real, que no se recupera."
        )

    motivo = motivo_no_puede_emitir(emisor)
    if motivo:
        # Se dice ya y no al emitir: si no, queda un borrador que nunca se va a
        # poder mandar y el motivo aparece dos pasos más tarde.
        raise ValueError(motivo)

    inicio, fin = siguiente_periodo(emisor)
    try:
        # El consecutivo lo decide de verdad la restricción de unicidad; el
        # savepoint deja seguir atendiendo la petición si choca.
        with transaction.atomic():
            return crear_nomina_prueba(
                emisor, consecutivo=consecutivo,
                periodo_inicio=inicio, periodo_fin=fin,
            )
    except IntegrityError:
        raise ValueError(
            f"El emisor ya tiene una nómina numerada "
            f"{PREFIJO_POR_DEFECTO}{consecutivo}. Use otro consecutivo o deje "
            f"que se asigne solo."
        )
