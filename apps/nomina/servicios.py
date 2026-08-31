"""Servicios de dominio de la nómina electrónica."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.nomina.models import Nomina, NominaConcepto

CERO = Decimal("0")

# Lo que la nota hereda del documento que ajusta. Son las condiciones del
# periodo y las partes: la nota de reemplazo repite **entero** el documento
# anterior (numeral 5.5.8), así que copiarlas es lo correcto y además evita el
# descuadre de que el ERP reenvíe a mano trece campos y uno salga distinto.
#
# Fuera quedan, a propósito: la identificación del documento (prefijo,
# consecutivo y número, que son suyos), los identificadores DIAN (CUNE, envío,
# trackId, estado, artefactos) y la ``Novedad``, que el XSD de la nota ni
# siquiera define.
CAMPOS_HEREDADOS = (
    "emisor",
    "empleado",
    # El ambiente se copia y no se resuelve del emisor: la nota apunta al CUNE
    # del documento anterior, que se firmó en uno concreto. Si el emisor ya pasó
    # a producción, una nota "de producción" sobre una nómina de habilitación
    # señalaría un documento que en ese ambiente no existe.
    "ambiente",
    "periodo_nomina",
    "fecha_liquidacion_inicio",
    "fecha_liquidacion_fin",
    "tiempo_laborado",
    "fecha_pago",
    "codigo_trabajador",
    "alto_riesgo_pension",
    "salario_integral",
    "sueldo",
    "fecha_retiro",
    "tipo_trabajador",
    "subtipo_trabajador",
    "tipo_contrato",
    "lugar_trabajo_pais",
    "lugar_trabajo_departamento",
    "lugar_trabajo_municipio",
    "lugar_trabajo_direccion",
    "forma_pago",
    "medio_pago",
    "banco",
    "tipo_cuenta",
    "numero_cuenta",
    "moneda",
    "trm",
)


def siguiente_consecutivo(emisor, prefijo) -> int:
    """El siguiente número libre del emisor para ese prefijo."""
    ultimo = Nomina.objects.filter(emisor=emisor, prefijo=prefijo).aggregate(
        ultimo=Max("consecutivo"),
    )["ultimo"]
    return (ultimo or 0) + 1


@transaction.atomic
def crear_nota_ajuste(nomina, *, tipo_nota, prefijo=None, consecutivo=None,
                      notas=None) -> Nomina:
    """Crea —solo crea— la nota de ajuste de una nómina ya emitida.

    La nota de nómina no es una nota crédito: no lleva diferencias sino el
    documento corregido completo (``TipoNota`` 1, *Reemplazar*) o nada más que
    la cabecera y el señalamiento del anterior (``TipoNota`` 2, *Eliminar*). Por
    eso esto clona: reemplazar significa repetir el documento entero, y el único
    sitio donde ese documento está completo y tal como se firmó es el propio
    predecesor.

    Queda en borrador. Para corregir algo —que es el motivo de la nota— se edita
    el borrador y después se llama a ``emitir`` y ``enviar``; tal cual sale es
    idéntica al original, que solo sirve para reemitir.

    La eliminación sale sin conceptos y con los totales en cero: su XML no lleva
    ni ``Devengados`` ni ``DevengadosTotal``, y los totales entran en el CUNE
    (ver ``docs/anexo-nomina.md`` §6).
    """
    if tipo_nota not in Nomina.TipoNota.values:
        raise ValueError(
            f"`tipo_nota` debe ser 1 (reemplazar) o 2 (eliminar), no {tipo_nota!r}."
        )
    if not nomina.cune:
        raise ValueError(
            "La nómina que se ajusta todavía no tiene CUNE: emítala antes de "
            "ajustarla."
        )

    es_eliminacion = tipo_nota == Nomina.TipoNota.ELIMINAR
    prefijo = nomina.prefijo if prefijo is None else prefijo
    if consecutivo is None:
        consecutivo = siguiente_consecutivo(nomina.emisor, prefijo)

    ahora = timezone.localtime()
    nota = Nomina(
        **{c: getattr(nomina, c) for c in CAMPOS_HEREDADOS},
        tipo_xml=Nomina.TipoXML.AJUSTE,
        tipo_nota=tipo_nota,
        nomina_predecesora=nomina,
        prefijo=prefijo,
        consecutivo=consecutivo,
        fecha_generacion=ahora.date(),
        hora_generacion=ahora.time(),
        notas=nomina.notas if notas is None else notas,
        total_devengados=CERO if es_eliminacion else nomina.total_devengados,
        total_deducciones=CERO if es_eliminacion else nomina.total_deducciones,
        redondeo=CERO if es_eliminacion else nomina.redondeo,
        total_comprobante=CERO if es_eliminacion else nomina.total_comprobante,
    )
    nota.save()

    if not es_eliminacion:
        NominaConcepto.objects.bulk_create(
            [_copiar_concepto(fila, nota) for fila in nomina.conceptos.all()]
        )
    return nota


def _copiar_concepto(fila, nota) -> NominaConcepto:
    """La misma línea colgando de la nota.

    Los campos se leen del modelo en vez de listarse aquí: la tabla de conceptos
    es la unión de los atributos de los 44 conceptos del anexo y crece cada vez
    que la DIAN añade uno, así que una lista escrita a mano se quedaría corta en
    silencio —y un devengado copiado a medias es un total que no cuadra—.
    """
    campos = [
        campo.attname
        for campo in NominaConcepto._meta.concrete_fields
        if campo.attname not in ("id", "nomina_id")
        and not getattr(campo, "auto_now", False)
        and not getattr(campo, "auto_now_add", False)
    ]
    return NominaConcepto(
        nomina=nota, **{campo: getattr(fila, campo) for campo in campos}
    )
