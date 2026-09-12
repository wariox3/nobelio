"""Documentos de prueba de facturación para la habilitación."""
import logging
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.catalogos.models import Moneda, Tributo, UnidadMedida
from apps.documentos.models import (
    Adquiriente,
    Documento,
    DocumentoDetalle,
    DocumentoDetalleImpuesto,
    DocumentoEstado,
    DocumentoTipo,
)
from apps.nucleo.models import Ambiente

from .emision import motivo_no_puede_emitir

logger = logging.getLogger(__name__)

# Importes del documento de prueba: mil pesos más su IVA del 19 %.
VALOR = Decimal("1000.00")
IVA = Decimal("190.00")

# Cuántas facturas deja el alta del software de facturación.
FACTURAS_DE_PRUEBA = 2


def _crear_documento(emisor, resolucion, *, codigo_tipo, consecutivo, observaciones,
                     **extra):
    """Arma un documento de prueba completo: cabecera, adquiriente y su línea.

    Lo comparten la factura y la nota crédito, que salvo el tipo, la referencia
    y el texto son el mismo documento. Estaba duplicado entero y las dos copias
    ya habían empezado a separarse.

    El adquiriente es el propio emisor: el Set de Pruebas no necesita un
    tercero real, y usar uno inventado mete datos de una persona que no existe
    en documentos que se envían de verdad.
    """
    documento = Documento.objects.create(
        documento_tipo=DocumentoTipo.objects.get(codigo=codigo_tipo),
        estado=DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.BORRADOR),
        emisor=emisor,
        ambiente=Documento.Ambiente.PRUEBAS,
        resolucion=resolucion,
        moneda=Moneda.objects.get(codigo="COP"),
        prefijo=resolucion.prefijo,
        consecutivo=consecutivo,
        fecha_emision=timezone.localdate(),
        hora_emision=timezone.localtime().time(),
        observaciones=observaciones,
        valor_bruto=VALOR,
        total_impuestos=IVA,
        total_a_pagar=VALOR + IVA,
        **extra,
    )

    adquiriente = Adquiriente.objects.create(
        documento=documento,
        razon_social=emisor.razon_social,
        tipo_identificacion=emisor.tipo_identificacion,
        numero_identificacion=emisor.numero_identificacion,
        tipo_organizacion=emisor.tipo_organizacion,
        pais=emisor.pais,
        departamento=emisor.departamento,
        municipio=emisor.municipio,
        direccion=emisor.direccion,
        correo=emisor.correo,
        telefono=emisor.telefono,
    )
    adquiriente.responsabilidades.set(emisor.responsabilidades.all())

    detalle = DocumentoDetalle.objects.create(
        documento=documento,
        numero_linea=1,
        descripcion="Servicio de prueba",
        cantidad=Decimal("1"),
        unidad_medida=UnidadMedida.objects.get(codigo="94"),
        valor_unitario=VALOR,
        valor_total=VALOR,
    )
    DocumentoDetalleImpuesto.objects.create(
        detalle=detalle,
        tributo=Tributo.objects.get(codigo="01"),
        tarifa=Decimal("19.00"),
        base_gravable=VALOR,
        valor=IVA,
    )
    return documento


@transaction.atomic
def crear_factura_prueba(emisor, resolucion, consecutivo=None):
    """Crea —solo crea— una factura de prueba y su nota crédito, en borrador.

    No las firma ni las envía. Es el material del Set de Pruebas: la nota anula
    la factura entera y la referencia, que es lo que la DIAN espera ver.

    Devuelve las dos, en ese orden.

    **Sin llamantes desde el 2026-09-12**, cuando se retiró el endpoint
    ``crear-factura-prueba``. El alta del software usa
    ``crear_facturas_de_prueba`` (solo facturas); esta es la única pieza que
    sabe armar la nota crédito del Set con su referencia, así que se conserva a
    la espera de decidir si se vuelve a exponer o se emite como un documento
    normal por ``POST /api/documentos/documento/``.
    """
    if consecutivo is None:
        consecutivo = 990000000

    factura = _crear_documento(
        emisor, resolucion,
        codigo_tipo=DocumentoTipo.Codigo.FACTURA_VENTA,
        consecutivo=consecutivo,
        observaciones="Documento del Set de Pruebas (habilitación).",
    )
    # La nota anula la factura entera (mismos importes) y la referencia por
    # `documento_referencia`, de donde el UBL saca DiscrepancyResponse y
    # BillingReference (ver apps.dian.ubl._ConstructorNotaUBL). Comparte
    # consecutivo con su factura: las notas heredan la numeración del documento
    # que corrigen, y el índice único incluye el tipo, así que no chocan.
    nota_credito = _crear_documento(
        emisor, resolucion,
        codigo_tipo=DocumentoTipo.Codigo.NOTA_CREDITO,
        consecutivo=consecutivo,
        observaciones="Nota crédito del Set de Pruebas (habilitación).",
        documento_referencia=factura,
        concepto_correccion=Documento.ConceptoNotaCredito.ANULACION,
    )
    return factura, nota_credito


def crear_facturas_de_prueba(emisor, resolucion, cantidad=FACTURAS_DE_PRUEBA):
    """Deja ``cantidad`` facturas de prueba en borrador. Devuelve la lista.

    Es lo que siembra el alta del software de facturación, para que el emisor
    no se quede con resolución y sin nada que emitir contra ella. Solo facturas:
    la nota crédito del Set de Pruebas se crea como un documento cualquiera por
    ``POST /api/documentos/documento/``, referenciando la factura que anula.

    Los consecutivos salen de ``resolucion.rango_desde`` —el primero que la
    DIAN autoriza— y no de un número fijo, para que valga con cualquier
    resolución y no solo con la del sandbox.

    **No duplica.** Si el emisor ya tiene facturas contra esa resolución se
    devuelve la lista vacía: el índice único (emisor, prefijo, consecutivo,
    tipo) rechazaría las repetidas, y el caso llega solo —dar de baja el
    software y volver a registrarlo pasa por aquí otra vez—.
    """
    ya_tiene = Documento.objects.filter(
        emisor=emisor,
        resolucion=resolucion,
        documento_tipo__codigo=DocumentoTipo.Codigo.FACTURA_VENTA,
    ).exists()
    if ya_tiene:
        return []

    desde = resolucion.rango_desde
    with transaction.atomic():
        return [
            _crear_documento(
                emisor, resolucion,
                codigo_tipo=DocumentoTipo.Codigo.FACTURA_VENTA,
                consecutivo=desde + i,
                observaciones="Documento del Set de Pruebas (habilitación).",
            )
            for i in range(cantidad)
        ]


def sembrar_documentos_de_prueba(emisor, tipo_software, resolucion):
    """Deja el material del Set de Pruebas que le toca a ese software.

    Lo llama el alta del software (``POST /api/emisores/software/``). Devuelve
    los documentos creados, que pueden ser ninguno.

    Cada operación se siembra a su manera porque se numera a su manera:

    - **Facturación**: facturas sobre la resolución. ``resolucion`` a ``None``
      es la señal de que no hay nada que hacer —sin numeración no hay documento
      posible—, y es lo que devuelve ``sembrar_resolucion_de_pruebas`` cuando
      el emisor ya está en producción.
    - **Nómina**: no se numera con resolución sino con prefijo y consecutivo
      propios, así que no hay `resolucion` que mirar y el ambiente se comprueba
      aquí, contra `ambiente_nomina` —cada operación tiene el suyo—.
    - **Documento equivalente**: espera a que se conozca su resolución de
      pruebas.

    Un catálogo sin cargar deja al emisor sin documentos de prueba, no sin
    software: son dos cosas distintas y solo una la pidió quien llama. Se anota
    en el log, que si no el hueco no se ve por ninguna parte.
    """
    from .nomina_prueba import crear_nominas_de_prueba

    tipo = str(tipo_software)
    try:
        # Savepoint propio: si esto falla, el software y su resolución se
        # quedan; sin él se vendría abajo la transacción entera.
        with transaction.atomic():
            if tipo == "facturacion":
                if resolucion is None:
                    return []
                return crear_facturas_de_prueba(emisor, resolucion)
            if tipo == "nomina":
                if emisor.ambiente_nomina != Ambiente.PRUEBAS:
                    return []
                return crear_nominas_de_prueba(emisor)
            return []
    except (ObjectDoesNotExist, IntegrityError, ValueError):
        logger.warning(
            "No se crearon los documentos de prueba del emisor %s para "
            "'%s' (¿faltan catálogos?).", emisor.pk, tipo, exc_info=True,
        )
        return []


# --- Un documento suelto, sobre la resolución que se indique -----------------

def tipo_de_documento_de(resolucion):
    """El tipo de documento que numera esa resolución.

    La resolución dice para qué numeración es en su ``tipo_factura``, y el
    código de ese catálogo es el mismo ``codigo_dian`` del tipo de documento
    —es el emparejamiento que ya usa ``_resolucion_por_numero`` al crear un
    documento—. Así el tipo no se elige a mano ni se da por supuesto: sale de
    la resolución.

    Lanza ``ValueError`` si esa numeración no corresponde a ningún documento
    que se numere con resolución. Es el caso de las notas (heredan el número
    del documento que corrigen, no llevan ``sts:InvoiceControl``) y el de los
    tipos de factura que el sistema todavía no emite.
    """
    codigo = resolucion.tipo_factura.codigo
    tipo = DocumentoTipo.objects.filter(codigo_dian=codigo).first()
    if tipo is None or tipo.codigo not in DocumentoTipo.CODIGOS_CON_RESOLUCION:
        raise ValueError(
            f"La resolución {resolucion.numero_resolucion} es de numeración "
            f"'{resolucion.tipo_factura.nombre}' ({codigo}), que no "
            f"corresponde a ningún documento que se numere con resolución. "
            f"Solo se crean documentos de prueba para facturación (01), "
            f"documento soporte (05) y documento equivalente (20)."
        )
    return tipo


def siguiente_consecutivo(resolucion, documento_tipo):
    """El primer número libre de la resolución para ese tipo.

    El siguiente al mayor que ya se usó, o el primero que la DIAN autoriza si
    aún no hay ninguno. Se mira por tipo porque el índice único lo incluye: una
    nota comparte el número de la factura que corrige y no gasta uno propio.
    """
    usado = Documento.objects.filter(
        resolucion=resolucion, documento_tipo=documento_tipo,
    ).aggregate(maximo=Max("consecutivo"))["maximo"]
    return resolucion.rango_desde if usado is None else usado + 1


def crear_documento_de_prueba(resolucion, consecutivo=None):
    """Un documento de prueba en borrador sobre ``resolucion``.

    El mismo que siembra el alta del software, pero de uno en uno y donde se
    diga. El tipo lo decide la resolución (``tipo_de_documento_de``) y el
    número, ``consecutivo``; si no se indica, el siguiente libre.

    Lanza ``ValueError`` con el motivo cuando no se puede: quien llama lo
    traduce a un 400.
    """
    documento_tipo = tipo_de_documento_de(resolucion)

    if documento_tipo.codigo == DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS:
        # El P.O.S. no es un documento más: exige el satélite `DocumentoPOS`
        # con la caja, el cajero y el código de venta, y sin él la validación
        # cruzada del serializer lo rechaza. Crearlo a medias sería dejar un
        # borrador que revienta al emitir.
        raise ValueError(
            "El documento equivalente P.O.S. necesita los datos de la caja "
            "(bloque 'pos'), que este endpoint no compone. Créelo con "
            "POST /api/documentos/documento/."
        )

    if not resolucion.activa:
        # Es justo lo que la bandera impide: numerar con una dada de baja.
        raise ValueError(
            f"La resolución {resolucion.numero_resolucion} está inactiva; no "
            f"numera documentos nuevos."
        )

    emisor = resolucion.emisor
    campo = Documento.CAMPO_AMBIENTE_EMISOR.get(
        documento_tipo.codigo, "ambiente_facturacion"
    )
    if getattr(emisor, campo) != Ambiente.PRUEBAS:
        # El documento sale sellado como de pruebas —lo fija `_crear_documento`—
        # pero gastaría un consecutivo del rango real, y esos no se recuperan.
        raise ValueError(
            f"El emisor {emisor.razon_social} ya está en producción para esta "
            f"operación. Un documento de prueba consumiría un consecutivo de "
            f"su numeración real, que no se recupera."
        )

    motivo = motivo_no_puede_emitir(emisor)
    if motivo:
        # Se dice ya y no al emitir: si no, queda un borrador que nunca se va
        # a poder mandar y el motivo aparece dos pasos más tarde.
        raise ValueError(motivo)

    if consecutivo is None:
        consecutivo = siguiente_consecutivo(resolucion, documento_tipo)
    if not (resolucion.rango_desde <= consecutivo <= resolucion.rango_hasta):
        raise ValueError(
            f"El consecutivo {consecutivo} no cabe en lo que autorizó la "
            f"resolución {resolucion.numero_resolucion} "
            f"({resolucion.rango_desde} a {resolucion.rango_hasta})."
        )

    try:
        # El índice único (emisor, prefijo, consecutivo, tipo) es quien decide
        # de verdad; el savepoint deja seguir atendiendo la petición si choca.
        with transaction.atomic():
            return _crear_documento(
                emisor, resolucion,
                codigo_tipo=documento_tipo.codigo,
                consecutivo=consecutivo,
                observaciones="Documento del Set de Pruebas (habilitación).",
            )
    except IntegrityError:
        raise ValueError(
            f"El emisor ya tiene un documento de este tipo numerado "
            f"{resolucion.prefijo}{consecutivo}."
        )
