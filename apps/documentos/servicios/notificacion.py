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
import logging
import zipfile
from io import BytesIO
from pathlib import PurePath

from django.template.loader import render_to_string

from django.conf import settings

from apps.dian.identificadores import nombre_archivo_dian
from apps.documentos.models import DocumentoEstado
from apps.nucleo.registro import campos
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

# Nombre que el destinatario ve como remitente. Es el de la plataforma y no el
# del emisor: el correo sale de la dirección de la pasarela, y firmarlo con el
# nombre de un tercero es lo que los filtros antispam leen como suplantación.
NOMBRE_REMITENTE = "RedDoc ERP"

# Identifica a este sistema entre los que usan la pasarela. Es fijo: no depende
# del ambiente ni del emisor, sino de quién hace la llamada.
APLICACION = "nobelio"


class ErrorNotificacion(Exception):
    """No se puede armar la notificación del documento."""


class ErrorEnvioCorreo(ErrorNotificacion):
    """Zinc aceptó la petición pero rechazó el envío.

    Responde 200 con ``error: true`` —correo bloqueado, dirección inválida, un
    fallo del proveedor de salida—, así que sin mirar ese campo un envío
    fallido pasaría por bueno.
    """


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
    """Asunto del correo con el formato que exige la DIAN.

    Cinco campos separados por ``;`` y sin espacios alrededor::

        NIT emisor;Razón social emisor;Número;Código del tipo;Razón social del PT

    No es decorativo: el receptor (o su software de recepción) lo parsea para
    clasificar el documento sin abrir el adjunto, así que el orden y el
    separador importan más que la legibilidad.

    El quinto campo es el proveedor tecnológico. En modalidad software propio
    —la de este proyecto— el proveedor es el propio emisor, igual que el
    ``ProviderID`` del XML, así que su razón social se repite.
    """
    emisor = documento.emisor
    return ";".join([
        emisor.numero_identificacion,
        emisor.razon_social,
        documento.numero,
        documento.documento_tipo.codigo_dian,
        emisor.razon_social,
    ])


def _moneda(valor):
    """Un importe como lo muestra la representación gráfica: ``1.785.000,00``."""
    return f"{valor:,.2f}".translate(str.maketrans(",.", ".,"))


def cuerpo_html(documento, paquete):
    """Renderiza el HTML del correo. La plantilla se edita sin tocar código."""
    return render_to_string(PLANTILLA_CORREO, {
        "documento": documento,
        # Formateado aquí y no en la plantilla: `floatformat` sigue la
        # localización de Django y saca "1785000,00", sin separador de miles.
        # Se usa el mismo formato que la representación gráfica.
        "total": _moneda(documento.total_a_pagar),
        "emisor": documento.emisor,
        "adquiriente": documento.adquiriente,
        "tipo_documento": documento.documento_tipo.nombre,
        "paquete": paquete,
        "lleva_pdf": any(n.endswith(".pdf") for n in paquete.archivos),
    })


def payload_zinc(documento, paquete, *, asunto=None, html=None):
    """Arma el cuerpo que espera Zinc en ``/api/correo/html``.

    Aislado a propósito: el contrato lo define Zinc, así que si cambian los
    nombres de los campos se corrige aquí y en ningún otro sitio. Los
    destinatarios van en una sola cadena separada por ``;``, que es como los
    parte la pasarela.
    """
    datos = {
        "correo": ";".join(_destinatarios(paquete)),
        "asunto": asunto if asunto is not None else asunto_notificacion(documento),
        "contenido": html if html is not None else cuerpo_html(documento, paquete),
        "nombreRemitente": getattr(
            settings, "ZINC_NOMBRE_REMITENTE", NOMBRE_REMITENTE
        ),
        # Identifican el documento en el registro de la pasarela, para poder
        # cruzar un correo con la factura sin abrir el adjunto.
        "aplicacion": APLICACION,
        # Identifica al emisor en el registro de envíos de la pasarela.
        "operador": documento.emisor_id,
        "documentoNumero": documento.numero,
        "documentoFecha": documento.fecha_emision.isoformat(),
        "adjuntos": [{
            "NombreArchivo": paquete.nombre,
            "B64": base64.b64encode(paquete.contenido).decode(),
        }],
    }
    # La copia es del emisor —su archivo, su contador—, no del documento, así
    # que se resuelve aquí y no viaja en la petición de notificar.
    if documento.emisor.correo_copia:
        datos["correoCopia"] = documento.emisor.correo_copia
    return datos


logger = logging.getLogger(__name__)


def _destinatarios(paquete):
    """Los correos a los que va el paquete, ya limpios."""
    return [c.strip() for c in paquete.destinatario.split(";") if c.strip()]


def enviar_notificacion(documento, *, pdf=None, adjuntos=(), zinc=None):
    """Arma el paquete, lo envía por correo y marca el documento como notificado.

    La marca va **después** del envío y solo si Zinc lo dio por bueno: si algo
    falla, el documento sigue sin notificar y se puede reintentar. El cliente se
    puede inyectar para las pruebas.

    Devuelve ``(paquete, respuesta)``; en la respuesta viene el ``codigoEnvio``
    con el que se rastrea el correo en la pasarela.
    """
    paquete = empaquetar_notificacion(documento, pdf=pdf, adjuntos=adjuntos)
    cliente = zinc or Zinc()
    respuesta = cliente.correo_html(payload_zinc(documento, paquete))
    if respuesta.get("error"):
        # WARNING y no ERROR: el documento sigue sin notificar y se puede
        # reintentar, que es justo lo que hace falta saber al leerlo.
        logger.warning("documento.notificacion_fallida %s", campos(
            documento=documento.pk,
            emisor=documento.emisor_id,
            numero=documento.numero,
            motivo=respuesta.get("errorMensaje") or "sin motivo",
        ))
        raise ErrorEnvioCorreo(
            respuesta.get("errorMensaje") or "Zinc rechazó el envío sin dar motivo."
        )
    marcar_notificado(documento)
    # Cuántos destinatarios, no cuáles: la dirección del adquiriente es un dato
    # de un tercero y el log se lee con menos cuidado que la base. Para saber a
    # quién se envió está el paquete; aquí basta con que salió y con el
    # `codigoEnvio`, que es con lo que se rastrea el correo en la pasarela.
    logger.info("documento.notificado %s", campos(
        documento=documento.pk,
        emisor=documento.emisor_id,
        numero=documento.numero,
        destinatarios=len(_destinatarios(paquete)),
        codigo_envio=respuesta.get("codigoEnvio"),
    ))
    return paquete, respuesta
