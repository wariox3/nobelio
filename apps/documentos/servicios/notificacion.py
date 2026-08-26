"""Empaquetado de la notificación al adquiriente.

Reúne lo que se le entrega al comprador —el AttachedDocument, la representación
gráfica y lo que el emisor quiera adjuntar— en un solo archivo listo para
enviar por correo. El envío en sí todavía no está implementado.

Lo que viaja es el AttachedDocument y no el XML pelado: es el formato de
entrega del anexo técnico, y dentro lleva el documento firmado junto al acuse
con el que la DIAN acredita que lo validó.
"""
import zipfile
from io import BytesIO
from pathlib import PurePath

from apps.documentos.models import DocumentoEstado

# Tope del material adjunto que acepta la notificación. No cuenta el XML, que
# lo pone el propio sistema y siempre viaja: el límite es para lo que sube el
# emisor, que es lo que puede hacer que el correo rebote.
TAMANO_MAXIMO_ADJUNTOS = 10 * 1024 * 1024  # 10 MB

MENSAJE_SIN_XML = (
    "El documento aún no está firmado: no hay nada que notificar."
)
MENSAJE_NO_ACEPTADO = (
    "Solo se notifica lo que la DIAN ya aceptó: entregarle al adquiriente un "
    "documento sin validar le haría creer que tiene una factura válida."
)
MENSAJE_SIN_CORREO = (
    "El adquiriente del documento no tiene correo electrónico; no hay a dónde "
    "notificar."
)


class ErrorNotificacion(Exception):
    """No se puede armar la notificación del documento."""


class Paquete:
    """Lo que se le va a enviar al adquiriente."""

    def __init__(self, nombre, contenido, tipo, destinatario, archivos):
        self.nombre = nombre
        self.contenido = contenido
        self.tipo = tipo
        self.destinatario = destinatario
        self.archivos = archivos

    @property
    def tamano(self):
        return len(self.contenido)


def _nombre_seguro(nombre, respaldo):
    """El nombre del archivo, sin rutas: dentro del zip solo va el archivo."""
    limpio = PurePath(nombre or "").name.strip()
    return limpio or respaldo


def _sin_repetir(nombre, usados):
    """Evita que dos adjuntos con el mismo nombre se pisen dentro del zip."""
    if nombre not in usados:
        usados.add(nombre)
        return nombre
    tallo = PurePath(nombre)
    for n in range(2, 1000):
        candidato = f"{tallo.stem}-{n}{tallo.suffix}"
        if candidato not in usados:
            usados.add(candidato)
            return candidato
    raise ErrorNotificacion(f"Demasiados adjuntos llamados {nombre}.")


def _attached_document(documento):
    """El contenedor firmado que se le entrega al adquiriente.

    Se importa aquí y no arriba para no atar esta app al pipeline DIAN al
    cargar el módulo: la notificación es un servicio de entrega, no de emisión.
    """
    from apps.dian.servicios import ErrorEmision, generar_attached_document

    try:
        return generar_attached_document(documento, exigir_acuse=True)
    except ErrorEmision as exc:
        raise ErrorNotificacion(str(exc)) from exc


def empaquetar_notificacion(documento, *, pdf=None, adjuntos=()):
    """Arma el paquete que se le entrega al adquiriente.

    Con adjuntos (el PDF cuenta como uno) se comprime todo junto en un zip; sin
    ellos se entrega el AttachedDocument tal cual, porque un zip de un solo
    archivo solo le añade un paso al que lo recibe.
    """
    if not documento.xml_archivo:
        raise ErrorNotificacion(MENSAJE_SIN_XML)
    if documento.estado_id and documento.estado.nombre != DocumentoEstado.Nombre.ACEPTADO:
        raise ErrorNotificacion(
            f"{MENSAJE_NO_ACEPTADO} El documento está en estado "
            f"'{documento.estado.nombre}'."
        )
    destinatario = getattr(documento.adquiriente, "correo", "")
    if not destinatario:
        raise ErrorNotificacion(MENSAJE_SIN_CORREO)

    contenedor = _attached_document(documento)
    nombre_contenedor = f"ad{documento.numero}.xml"
    adjuntos = list(adjuntos)

    if pdf is None and not adjuntos:
        return Paquete(
            nombre=nombre_contenedor, contenido=contenedor, tipo="application/xml",
            destinatario=destinatario, archivos=[nombre_contenedor],
        )

    buffer = BytesIO()
    usados = set()
    incluidos = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_sin_repetir(nombre_contenedor, usados), contenedor)
        incluidos.append(nombre_contenedor)
        if pdf is not None:
            nombre = _sin_repetir(
                _nombre_seguro(getattr(pdf, "name", ""), f"{documento.numero}.pdf"),
                usados,
            )
            zf.writestr(nombre, pdf.read())
            incluidos.append(nombre)
        for indice, adjunto in enumerate(adjuntos, start=1):
            nombre = _sin_repetir(
                _nombre_seguro(getattr(adjunto, "name", ""), f"adjunto-{indice}"),
                usados,
            )
            zf.writestr(nombre, adjunto.read())
            incluidos.append(nombre)

    return Paquete(
        nombre=f"{documento.numero}.zip", contenido=buffer.getvalue(),
        tipo="application/zip", destinatario=destinatario, archivos=incluidos,
    )
