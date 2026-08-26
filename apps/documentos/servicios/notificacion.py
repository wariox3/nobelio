"""Empaquetado de la notificación al adquiriente.

Reúne lo que se le entrega al comprador —el XML firmado, la representación
gráfica y lo que el emisor quiera adjuntar— en un solo archivo listo para
enviar por correo. El envío en sí todavía no está implementado.
"""
import zipfile
from io import BytesIO
from pathlib import PurePath

# Tope del material adjunto que acepta la notificación. No cuenta el XML, que
# lo pone el propio sistema y siempre viaja: el límite es para lo que sube el
# emisor, que es lo que puede hacer que el correo rebote.
TAMANO_MAXIMO_ADJUNTOS = 10 * 1024 * 1024  # 10 MB

MENSAJE_SIN_XML = (
    "El documento aún no está firmado: no hay XML que notificar."
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


def empaquetar_notificacion(documento, *, pdf=None, adjuntos=()):
    """Arma el paquete que se le entrega al adquiriente.

    Con adjuntos (el PDF cuenta como uno) se comprime todo junto en un zip; sin
    ellos se entrega el XML tal cual, porque un zip de un solo archivo solo le
    añade un paso al que lo recibe.
    """
    if not documento.xml_archivo:
        raise ErrorNotificacion(MENSAJE_SIN_XML)
    destinatario = getattr(documento.adquiriente, "correo", "")
    if not destinatario:
        raise ErrorNotificacion(MENSAJE_SIN_CORREO)

    xml = documento.leer_xml()
    nombre_xml = f"{documento.numero}.xml"
    adjuntos = list(adjuntos)

    if pdf is None and not adjuntos:
        return Paquete(
            nombre=nombre_xml, contenido=xml, tipo="application/xml",
            destinatario=destinatario, archivos=[nombre_xml],
        )

    buffer = BytesIO()
    usados = set()
    incluidos = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_sin_repetir(nombre_xml, usados), xml)
        incluidos.append(nombre_xml)
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
