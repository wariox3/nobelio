"""Empaquetado de la notificación al adquiriente.

Reúne lo que se le entrega al comprador —el AttachedDocument, la representación
gráfica y lo que el emisor quiera adjuntar— en un zip listo para enviar por
correo. El envío en sí todavía no está implementado.

Siempre es un zip, aunque solo lleve el contenedor: es el formato en el que se
entrega la factura electrónica, y el receptor (o su software) espera abrir un
zip, no un archivo suelto.

Lo que viaja es el AttachedDocument y no el XML pelado: es el formato de
entrega del anexo técnico, y dentro lleva el documento firmado junto al acuse
con el que la DIAN acredita que lo validó.
"""
import base64
import zipfile
from io import BytesIO
from pathlib import PurePath

from django.template.loader import render_to_string

from apps.dian.identificadores import nombre_archivo_dian
from apps.documentos.models import DocumentoEstado
from apps.utilidades.zinc import Zinc

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


PLANTILLA_CORREO = "documentos/notificacion_correo.html"


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


def nombre_dian(documento, prefijo):
    """El nombre DIAN del documento para un prefijo dado, sin extensión."""
    return nombre_archivo_dian(
        prefijo,
        nit=documento.emisor.numero_identificacion,
        consecutivo=documento.consecutivo,
    )


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


def marcar_notificado(documento):
    """Deja constancia de que el documento ya se le entregó al adquiriente.

    Se llama cuando la notificación sale bien. El día que exista el envío por
    correo, la llamada se mueve a después de que el correo salga: la marca debe
    significar "llegó", no "se armó".
    """
    if documento.notificado:
        return
    documento.notificado = True
    documento.save(update_fields=["notificado", "actualizado_en"])


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

    Dentro va siempre el AttachedDocument; el PDF y los adjuntos se suman si
    vienen. El zip y los dos archivos que pone el sistema se nombran con la
    convención DIAN (ver ``nombre_dian``); los adjuntos del emisor conservan su
    nombre, que es suyo y significa algo para el receptor.
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
    nombre_contenedor = f"{nombre_dian(documento, 'ad')}.xml"
    # El PDF no tiene prefijo propio en la convención DIAN, así que toma el del
    # paquete, como hacen los proveedores tecnológicos.
    base_paquete = nombre_dian(documento, "z")
    adjuntos = list(adjuntos)

    buffer = BytesIO()
    usados = set()
    incluidos = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_sin_repetir(nombre_contenedor, usados), contenedor)
        incluidos.append(nombre_contenedor)
        if pdf is not None:
            nombre = _sin_repetir(f"{base_paquete}.pdf", usados)
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
        nombre=f"{base_paquete}.zip", contenido=buffer.getvalue(),
        tipo="application/zip", destinatario=destinatario, archivos=incluidos,
    )


# ---------------------------------------------------------------------------
# Envío por correo
# ---------------------------------------------------------------------------
def asunto_notificacion(documento):
    """Asunto del correo: lo que el receptor ve en su bandeja.

    Lleva emisor, tipo y número porque quien recibe facturas de varios
    proveedores las busca por ahí, no abriendo adjuntos.
    """
    return (
        f"{documento.emisor.razon_social} - "
        f"{documento.documento_tipo.nombre} {documento.numero}"
    )


def cuerpo_html(documento, paquete):
    """Renderiza el HTML del correo. La plantilla se edita sin tocar código."""
    return render_to_string(PLANTILLA_CORREO, {
        "documento": documento,
        "emisor": documento.emisor,
        "adquiriente": documento.adquiriente,
        "tipo_documento": documento.documento_tipo.nombre,
        "paquete": paquete,
        "lleva_pdf": any(n.endswith(".pdf") for n in paquete.archivos),
    })


def payload_zinc(documento, paquete, *, asunto=None, html=None):
    """Arma el cuerpo que espera Zinc en ``/api/correo/html``.

    Aislado a propósito: el contrato lo define Zinc, así que si cambian los
    nombres de los campos se corrige aquí y en ningún otro sitio.
    """
    return {
        "destinatarios": [paquete.destinatario],
        "asunto": asunto if asunto is not None else asunto_notificacion(documento),
        "html": html if html is not None else cuerpo_html(documento, paquete),
        "adjuntos": [{
            "nombre": paquete.nombre,
            "tipo": paquete.tipo,
            "contenido": base64.b64encode(paquete.contenido).decode(),
        }],
    }


def enviar_notificacion(documento, *, pdf=None, adjuntos=(), zinc=None):
    """Arma el paquete, lo envía por correo y marca el documento como notificado.

    La marca va **después** del envío: si Zinc falla, el documento sigue sin
    notificar y se puede reintentar. El cliente se puede inyectar para las
    pruebas.
    """
    paquete = empaquetar_notificacion(documento, pdf=pdf, adjuntos=adjuntos)
    cliente = zinc or Zinc()
    respuesta = cliente.correo_html(payload_zinc(documento, paquete))
    marcar_notificado(documento)
    return paquete, respuesta
