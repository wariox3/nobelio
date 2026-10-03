"""Procesamiento de un correo recibido: lo que hace la tarea ``procesar_correo``.

Por ahora solo las cabeceras: descarga el MIME de R2, guarda el asunto y el
Message-ID, y reconoce la confirmación de reenvío de Gmail. Los adjuntos y los
documentos son el paso siguiente (``docs/recepcion.md``).
"""
import logging
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from botocore.exceptions import BotoCoreError, ClientError
from django.db.models import F

from apps.nucleo.registro import campos
from apps.recepcion import r2
from apps.recepcion.models import Correo

logger = logging.getLogger(__name__)

LARGO_CABECERA = 998
REMITENTE_GMAIL = "forwarding-noreply@google.com"
# Gmail pone el código en el asunto, "(#123456789) Confirmación de reenvío...",
# y el enlace en el cuerpo.
CODIGO_GMAIL = re.compile(r"\(#(\d+)\)")
ENLACE_GMAIL = re.compile(r"https://mail(?:-settings)?\.google\.com/mail/\S+")

# Lo que ya terminó no se vuelve a procesar: la tarea puede correr dos veces
# (``acks_late``) y el endpoint puede reencolar un correo que sigue pendiente.
ESTADOS_PROCESABLES = {Correo.Estado.PENDIENTE, Correo.Estado.ERROR}


class ErrorTransitorio(Exception):
    """Un fallo que puede arreglarse solo (R2 caído, red): se reintenta."""


class ErrorPermanente(Exception):
    """Un fallo que no se arregla reintentando: el correo queda en ``error``."""


def procesar(correo_id):
    """Procesa el correo ``correo_id``.

    Lanza ``ErrorTransitorio`` para que la tarea reintente; cualquier otro
    fallo deja el correo en ``error`` con su detalle.
    """
    correo = Correo.objects.filter(pk=correo_id).first()
    if correo is None or correo.estado not in ESTADOS_PROCESABLES:
        # Se eliminó mientras esperaba en la cola, o ya se procesó.
        return
    Correo.objects.filter(pk=correo_id).update(intentos=F("intentos") + 1)
    try:
        mime = _descargar(correo.raw_key)
        _aplicar_cabeceras(correo, mime)
    except ErrorPermanente as error:
        marcar_error(correo_id, str(error))
        return
    correo.error_detalle = ""
    correo.save(update_fields=[
        "asunto", "message_id", "estado", "confirmacion_reenvio", "error_detalle",
    ])
    logger.info("recepcion.correo_procesado %s", campos(
        correo=correo.pk, estado=correo.estado, emisor=correo.emisor_id,
    ))


def marcar_error(correo_id, detalle):
    """Deja el correo en ``error`` con ``detalle``: lo usa también la tarea al
    agotar los reintentos."""
    Correo.objects.filter(pk=correo_id).update(
        estado=Correo.Estado.ERROR, error_detalle=detalle,
    )
    logger.warning("recepcion.correo_error %s", campos(correo=correo_id, detalle=detalle))


def _descargar(raw_key):
    try:
        return r2.descargar_mime(raw_key)
    except r2.R2NoConfigurado:
        raise ErrorPermanente("El almacenamiento R2 no está configurado (R2_*).")
    except ClientError as error:
        codigo = error.response.get("Error", {}).get("Code", "")
        if codigo in ("NoSuchKey", "404"):
            raise ErrorPermanente(f"El correo no está en R2: {raw_key}.")
        if codigo in ("AccessDenied", "403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            raise ErrorPermanente(f"R2 rechazó las credenciales ({codigo}).")
        raise ErrorTransitorio(f"R2 respondió {codigo or 'un error'}.")
    except BotoCoreError as error:
        raise ErrorTransitorio(f"No se pudo conectar con R2: {error}.")


def _aplicar_cabeceras(correo, mime):
    """Llena asunto, Message-ID y estado a partir del MIME."""
    mensaje = BytesParser(policy=policy.default).parsebytes(mime)
    correo.asunto = _cabecera(mensaje, "subject")
    correo.message_id = _cabecera(mensaje, "message-id")
    remitente = parseaddr(_cabecera(mensaje, "from"))[1].lower()
    if remitente == REMITENTE_GMAIL:
        correo.estado = Correo.Estado.CONFIRMACION_REENVIO
        correo.confirmacion_reenvio = _confirmacion_gmail(correo.asunto, mensaje)
    else:
        correo.estado = Correo.Estado.PROCESADO


def _cabecera(mensaje, nombre):
    """La cabecera decodificada (``=?utf-8?...?=``) y en una línea, o ``""``.

    Una cabecera mal formada no tumba el procesamiento: se guarda vacía.
    """
    try:
        valor = mensaje.get(nombre)
    except Exception:
        return ""
    if valor is None:
        return ""
    return " ".join(str(valor).split())[:LARGO_CABECERA]


def _confirmacion_gmail(asunto, mensaje):
    """El código y el enlace de la confirmación de reenvío, en dos líneas."""
    lineas = []
    if codigo := CODIGO_GMAIL.search(asunto):
        lineas.append(f"codigo: {codigo.group(1)}")
    cuerpo = mensaje.get_body(preferencelist=("plain", "html"))
    texto = cuerpo.get_content() if cuerpo is not None else ""
    if enlace := ENLACE_GMAIL.search(texto):
        # El \S+ se lleva la puntuación que cierra la frase o la etiqueta HTML.
        lineas.append("enlace: " + enlace.group(0).rstrip('.,;)>"'))
    return "\n".join(lineas)
