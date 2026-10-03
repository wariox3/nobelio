"""Guardar y borrar los archivos de los correos de recepción (``Adjunto``).

Los archivos viven en B2 y las filas en la base, y las dos cosas no caben en
una sola transacción. Las reglas para que no quede nada a medias:

- Al guardar, quien llama lleva la cuenta de lo que subió (``subidos``) y, si
  la transacción falla, lo borra con :func:`borrar_subidos`.
- Al borrar, las filas se borran en una transacción y los archivos dentro de
  ella, después: si B2 o R2 fallan, las filas vuelven y repetir termina el
  trabajo, porque borrar lo que ya no existe no es error.
"""
import hashlib
import logging
import mimetypes
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.db import transaction

from apps.nucleo.registro import campos
from apps.recepcion import r2
from apps.recepcion.models import Adjunto, Correo, Documento

logger = logging.getLogger(__name__)

LARGO_NOMBRE = 255
# Fijos: `mimetypes` lee la configuración del sistema, y un XML sale como
# text/xml en un servidor y application/xml en otro.
TIPOS = {".xml": "application/xml", ".pdf": "application/pdf"}


def nombre_seguro(nombre):
    """Solo el nombre del archivo, sin rutas ni caracteres de control.

    El que pone el proveedor puede traer ``../../`` o una ruta de Windows: no
    llega al bucket (allá el nombre es un uuid), pero sí a la descarga.
    """
    nombre = PurePosixPath((nombre or "").replace("\\", "/")).name
    nombre = "".join(c for c in nombre if c.isprintable()).strip()
    return nombre[:LARGO_NOMBRE] or "adjunto"


def tipo_de(nombre):
    extension = PurePosixPath(nombre.lower()).suffix
    return TIPOS.get(extension) or mimetypes.guess_type(nombre)[0] or "application/octet-stream"


def crear(correo, contenido, *, nombre, rol, documento=None, subidos):
    """Sube ``contenido`` a B2 y crea su ``Adjunto``.

    Agrega el archivo subido a ``subidos`` antes de crear la fila, para que
    quien llama pueda borrarlo si algo falla después.
    """
    nombre = nombre_seguro(nombre)
    adjunto = Adjunto(
        correo=correo,
        documento=documento,
        rol=rol,
        nombre=nombre,
        tipo_contenido=tipo_de(nombre),
        tamano=len(contenido),
        sha256=hashlib.sha256(contenido).hexdigest(),
    )
    adjunto.archivo.save(nombre, ContentFile(contenido), save=False)
    subidos.append(adjunto.archivo)
    adjunto.save()
    return adjunto


def borrar_subidos(subidos):
    """Borra de B2 lo que se subió en un intento que no terminó."""
    for archivo in subidos:
        try:
            archivo.storage.delete(archivo.name)
        except Exception:
            logger.exception("recepcion.archivo_no_borrado %s", campos(nombre=archivo.name))


def eliminar_correo(correo):
    """Borra el correo, sus documentos, sus adjuntos en B2 y su MIME en R2.

    Devuelve ``(documentos, adjuntos)``: cuántos se borraron. Lanza
    ``r2.R2NoConfigurado`` antes de tocar nada si faltan las ``R2_*``, y deja
    subir los errores de B2 y R2 (la transacción se deshace).
    """
    if not r2.r2_habilitado():
        raise r2.R2NoConfigurado
    with transaction.atomic():
        correo = Correo.objects.select_for_update().get(pk=correo.pk)
        adjuntos = list(Adjunto.objects.filter(correo=correo))
        # Los adjuntos primero: protegen a documentos y correo.
        Adjunto.objects.filter(correo=correo).delete()
        documentos, _ = Documento.objects.filter(correo=correo).delete()
        raw_key = correo.raw_key
        correo.delete()
        for adjunto in adjuntos:
            adjunto.archivo.storage.delete(adjunto.archivo.name)
        r2.borrar_mime(raw_key)
    return documentos, len(adjuntos)
