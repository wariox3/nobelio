"""Habilitación: emitir el Set de Pruebas hasta su veredicto.

Lo usa ``POST /api/emisores/software/{id}/habilitar/``, **en la propia
petición**, para el software de facturación y el de nómina:

- **Facturación**: las facturas de venta y después las notas crédito.
- **Nómina**: las nóminas y después las notas de ajuste.

Emite lo que esté pendiente y consulta lo enviado hasta que cada documento
quede aceptado o rechazado. Una nota espera a que su documento esté aceptado,
porque lleva su CUFE/CUNE. Si la DIAN no responde, o se acaba el tiempo de la
petición, se corta y se devuelve cómo quedó cada uno: se vuelve a llamar al
endpoint y sigue desde ahí.

Cada documento pasa por la misma función que su `emitir/` —con el mismo
bloqueo—, así que nada se envía dos veces aunque se llame al endpoint dos veces
seguidas o a la vez que `emitir/`.
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests
from django.utils import timezone

from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.emisores.models import SoftwareDian
from apps.nomina.models import Nomina
from apps.nucleo.models import Ambiente
from apps.nucleo.registro import campos

logger = logging.getLogger(__name__)

# Tiempo máximo de trabajo por petición. Por debajo del `--timeout 120` de
# gunicorn (docs/despliegue.md), con margen para una llamada a la DIAN que
# empiece justo antes del límite: si no, gunicorn mata la petición a mitad y
# el cliente no ve en qué estado quedó nada.
PRESUPUESTO_SEGUNDOS = 75

# Pausa entre consultas mientras haya documentos enviados sin veredicto.
ESPERA_SEGUNDOS = 5

TERMINALES = (DocumentoEstado.Nombre.ACEPTADO, DocumentoEstado.Nombre.RECHAZADO)

# Por qué terminó una llamada.
SET_ACEPTADO = "set_aceptado"              # la DIAN dio el Set por aceptado
SIN_PENDIENTES = "sin_pendientes"          # todo aceptado o rechazado
CON_ERRORES = "con_errores"                # lo que queda no se pudo emitir (ver `error`)
DIAN_SIN_RESPUESTA = "dian_sin_respuesta"  # se cortó: volver a llamar
TIEMPO_AGOTADO = "tiempo_agotado"          # se cortó: volver a llamar

MOTIVOS = (SET_ACEPTADO, SIN_PENDIENTES, CON_ERRORES, DIAN_SIN_RESPUESTA, TIEMPO_AGOTADO)
MOTIVOS_TERMINADO = (SET_ACEPTADO, SIN_PENDIENTES)


@dataclass(frozen=True)
class _Set:
    """Lo que cambia de un Set de Pruebas a otro."""

    documentos: Callable          # emisor -> queryset, en orden de emisión
    referencia: Callable          # documento -> el que corrige, o None
    tipo: Callable                # documento -> etiqueta para la respuesta
    emitir: Callable              # documento -> lo lleva un paso adelante
    preparar: Callable            # borrador -> lo deja listo para firmar


# --- Facturación -------------------------------------------------------------

def _documentos_de_facturacion(emisor):
    """Facturas y notas crédito de pruebas: primero las facturas.

    (``factura_venta`` va antes que ``nota_credito`` en el orden alfabético.)
    """
    return (
        Documento.objects.filter(
            emisor=emisor,
            ambiente=Ambiente.PRUEBAS,
            documento_tipo__codigo__in=(
                DocumentoTipo.Codigo.FACTURA_VENTA,
                DocumentoTipo.Codigo.NOTA_CREDITO,
            ),
        )
        .select_related("estado", "documento_tipo", "documento_referencia__estado")
        .order_by("documento_tipo__codigo", "consecutivo")
    )


def _poner_fecha_de_hoy(documento):
    """Un borrador de pruebas se firma con la fecha de hoy (regla FAD09).

    Los documentos del Set se siembran al registrar el software, que puede
    haber sido otro día; sin esto, `generar_y_firmar` los rechaza. Solo se toca
    un borrador: lo firmado ya lleva su fecha dentro del CUFE.
    """
    hoy = timezone.localdate()
    if documento.fecha_emision != hoy:
        documento.fecha_emision = hoy
        documento.save(update_fields=["fecha_emision", "actualizado_en"])


def _emitir_documento(documento):
    from apps.dian import servicios as dian

    return dian.emitir(documento)


FACTURACION = _Set(
    documentos=_documentos_de_facturacion,
    referencia=lambda d: d.documento_referencia,
    tipo=lambda d: d.documento_tipo.codigo,
    emitir=_emitir_documento,
    preparar=_poner_fecha_de_hoy,
)


# --- Nómina ------------------------------------------------------------------

def _documentos_de_nomina(emisor):
    """Nóminas y notas de ajuste de pruebas: primero las nóminas.

    (``tipo_xml`` 102 es la nómina y 103 la nota de ajuste.) La nómina no
    necesita refecharse: `generar_y_firmar_nomina` fija su fecha de generación
    al firmar.
    """
    return (
        Nomina.objects.filter(emisor=emisor, ambiente=Ambiente.PRUEBAS)
        .select_related("estado", "nomina_predecesora__estado")
        .order_by("tipo_xml", "consecutivo")
    )


def _emitir_nomina(nomina):
    from apps.dian import servicios as dian

    return dian.emitir_nomina(nomina)


NOMINA = _Set(
    documentos=_documentos_de_nomina,
    referencia=lambda n: n.nomina_predecesora,
    tipo=lambda n: "nota_ajuste" if n.tipo_xml == Nomina.TipoXML.AJUSTE else "nomina",
    emitir=_emitir_nomina,
    preparar=lambda n: None,
)

SET_POR_TIPO_DE_SOFTWARE = {
    SoftwareDian.Tipo.FACTURACION: FACTURACION,
    SoftwareDian.Tipo.NOMINA: NOMINA,
}


def set_de(software):
    """El Set de Pruebas que se habilita por aquí, o ``None`` si no hay."""
    return SET_POR_TIPO_DE_SOFTWARE.get(software.tipo)


# --- El proceso, común a los dos ---------------------------------------------

def _terminado(documento):
    return documento.estado.nombre in TERMINALES


def _bloqueado(conjunto, documento):
    """Corrige un documento que la DIAN rechazó: nunca se podrá firmar."""
    referencia = conjunto.referencia(documento)
    return (
        referencia is not None
        and referencia.estado.nombre == DocumentoEstado.Nombre.RECHAZADO
    )


def _en_espera(conjunto, documento):
    """Corrige un documento que aún no tiene veredicto."""
    referencia = conjunto.referencia(documento)
    return referencia is not None and referencia.estado.nombre not in TERMINALES


def resumen(conjunto, documento, error=""):
    return {
        "id": str(documento.pk),
        "numero": documento.numero,
        "tipo": conjunto.tipo(documento),
        "estado": documento.estado.nombre,
        "error": error,
    }


def habilitar(software, *, presupuesto=PRESUPUESTO_SEGUNDOS, espera=ESPERA_SEGUNDOS):
    """Emite y consulta el Set de Pruebas hasta su veredicto, dentro del presupuesto.

    Devuelve ``(motivo, documentos)``: por qué terminó (las constantes de
    arriba) y cada documento con su estado y, si no se pudo emitir, el motivo.

    En cada vuelta, cada documento sin veredicto pasa por su `emitir`: el
    borrador se firma y se envía, el firmado se envía y el enviado se consulta.
    Una nota espera a que su documento esté aceptado; si se rechazó, la nota ya
    no se puede emitir y cuenta como terminada.

    - Un documento que no se puede emitir (``ErrorEmision`` o ``ValueError``)
      se anota y no se reintenta en esta llamada: repetirlo daría el mismo
      error.
    - Si la DIAN no responde (``RequestException``) se corta todo: el documento
      queda donde quedó —firmado, o enviado sin veredicto— y se vuelve a llamar.
    """
    from apps.dian import servicios as dian

    conjunto = set_de(software)
    limite = time.monotonic() + presupuesto
    errores = {}

    def _set_aceptado():
        software.refresh_from_db(fields=["set_pruebas_aceptado"])
        return software.set_pruebas_aceptado

    def _salida(motivo):
        documentos = conjunto.documentos(software.emisor)
        return motivo, [resumen(conjunto, d, errores.get(d.pk, "")) for d in documentos]

    while True:
        if _set_aceptado():
            return _salida(SET_ACEPTADO)

        documentos = list(conjunto.documentos(software.emisor))
        pendientes = [
            d for d in documentos if not _terminado(d) and not _bloqueado(conjunto, d)
        ]
        if not pendientes:
            return _salida(SIN_PENDIENTES)
        por_avanzar = [
            d for d in pendientes
            if d.pk not in errores and not _en_espera(conjunto, d)
        ]
        if not por_avanzar:
            # Lo que queda o dio error o espera a un documento que dio error.
            return _salida(CON_ERRORES)

        for documento in por_avanzar:
            if time.monotonic() >= limite:
                return _salida(TIEMPO_AGOTADO)
            try:
                if documento.estado.nombre == DocumentoEstado.Nombre.BORRADOR:
                    conjunto.preparar(documento)
                conjunto.emitir(documento)
            except (dian.ErrorEmision, ValueError) as exc:
                errores[documento.pk] = str(exc)
                logger.warning("habilitacion.no_emitible %s", campos(
                    software=software.pk, documento=documento.pk, motivo=str(exc),
                ))
            except requests.RequestException as exc:
                errores[documento.pk] = f"La DIAN no respondió: {exc}"
                logger.warning("habilitacion.sin_respuesta %s", campos(
                    software=software.pk, documento=documento.pk, motivo=str(exc),
                ))
                return _salida(DIAN_SIN_RESPUESTA)
            # La aceptación del Set puede llegar en la consulta de cualquiera.
            if _set_aceptado():
                return _salida(SET_ACEPTADO)

        # Solo se espera si hay algo enviado sin veredicto: una nota que se
        # acaba de desbloquear se emite en la vuelta siguiente sin pausa.
        hay_enviados = type(pendientes[0]).objects.filter(
            pk__in=[d.pk for d in pendientes],
            estado__nombre=DocumentoEstado.Nombre.ENVIADO,
        ).exists()
        if hay_enviados:
            if time.monotonic() + espera >= limite:
                return _salida(TIEMPO_AGOTADO)
            time.sleep(espera)
