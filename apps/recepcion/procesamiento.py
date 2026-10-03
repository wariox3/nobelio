"""Procesamiento de un correo recibido: lo que hace la tarea ``procesar_correo``.

Descarga el MIME de R2, guarda el asunto y el Message-ID, reconoce la
confirmación de reenvío de Gmail y registra los documentos electrónicos que
trae (``extraccion``):

- Cada documento va al emisor del **NIT receptor del XML**, aunque el correo
  haya llegado al buzón de otro. Si ese NIT no es de ningún emisor, no se guarda.
- Un CUFE que ya existe se ignora, sin marcarlo.
- El correo queda ``procesado`` si trajo al menos un documento de un emisor
  (nuevo o repetido), ``sin_documentos`` si no trajo ninguno y
  ``empresa_desconocida`` si los que trajo no son de ningún emisor.
"""
import logging
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from botocore.exceptions import BotoCoreError, ClientError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F

from apps.catalogos.models import Moneda
from apps.documentos.models import DocumentoTipo
from apps.emisores.models import Emisor
from apps.nucleo.registro import campos
from apps.recepcion import extraccion, r2
from apps.recepcion.models import Correo, Documento

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
        mensaje = BytesParser(policy=policy.default).parsebytes(mime)
        _aplicar_cabeceras(correo, mensaje)
        if correo.estado != Correo.Estado.CONFIRMACION_REENVIO:
            _registrar_documentos(correo, mensaje)
    except ErrorPermanente as error:
        marcar_error(correo_id, str(error))
        return
    correo.error_detalle = ""
    correo.save(update_fields=[
        "asunto", "message_id", "estado", "confirmacion_reenvio", "error_detalle",
        "emisor",
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


def _aplicar_cabeceras(correo, mensaje):
    """Llena asunto y Message-ID, y reconoce la confirmación de Gmail."""
    correo.asunto = _cabecera(mensaje, "subject")
    correo.message_id = _cabecera(mensaje, "message-id")
    remitente = parseaddr(_cabecera(mensaje, "from"))[1].lower()
    if remitente == REMITENTE_GMAIL:
        correo.estado = Correo.Estado.CONFIRMACION_REENVIO
        correo.confirmacion_reenvio = _confirmacion_gmail(correo.asunto, mensaje)


def _registrar_documentos(correo, mensaje):
    """Guarda los documentos del correo y deja su estado."""
    try:
        documentos = extraccion.documentos_del_correo(mensaje)
    except extraccion.ContenidoExcesivo as error:
        raise ErrorPermanente(str(error))
    nuevos = repetidos = desconocidos = 0
    for datos in documentos:
        emisor = Emisor.objects.filter(
            numero_identificacion=datos.receptor_numero_identificacion,
        ).first()
        if emisor is None:
            desconocidos += 1
            continue
        if correo.emisor_id is None:
            # Llegó a un buzón que no es un NIT registrado, pero el XML dice
            # de quién es.
            correo.emisor = emisor
        if _guardar(datos, emisor, correo):
            nuevos += 1
        else:
            repetidos += 1
    if not documentos:
        correo.estado = Correo.Estado.SIN_DOCUMENTOS
    elif nuevos or repetidos:
        correo.estado = Correo.Estado.PROCESADO
    else:
        correo.estado = Correo.Estado.EMPRESA_DESCONOCIDA
    logger.info("recepcion.documentos %s", campos(
        correo=correo.pk, nuevos=nuevos, repetidos=repetidos, desconocidos=desconocidos,
    ))


def _guardar(datos, emisor, correo):
    """Crea el documento con sus archivos en B2. ``False`` si el CUFE ya existía.

    Los archivos se suben antes de crear la fila (el ``upload_to`` necesita el
    emisor y la fecha), y si la fila no se crea se borran: un CUFE repetido no
    deja archivos sueltos en el bucket.
    """
    if Documento.objects.filter(cufe_cude=datos.cufe_cude).exists():
        return False
    documento = Documento(
        numero=datos.numero,
        cufe_cude=datos.cufe_cude,
        fecha_emision=datos.fecha_emision,
        hora_emision=datos.hora_emision,
        tipo_codigo_dian=datos.tipo_codigo_dian,
        proveedor_numero_identificacion=datos.proveedor_numero_identificacion,
        proveedor_digito_verificacion=datos.proveedor_digito_verificacion,
        proveedor_razon_social=datos.proveedor_razon_social,
        receptor_numero_identificacion=datos.receptor_numero_identificacion,
        valor_bruto=datos.valor_bruto,
        total_impuestos=datos.total_impuestos,
        total_a_pagar=datos.total_a_pagar,
        validacion_codigo=datos.validacion_codigo,
        fecha_validacion=datos.fecha_validacion,
        documento_tipo=DocumentoTipo.objects.get(codigo=datos.tipo),
        moneda=Moneda.objects.filter(codigo=datos.moneda).first() if datos.moneda else None,
        emisor=emisor,
        correo=correo,
    )
    base = _nombre_archivo(datos)
    try:
        documento.xml_archivo.save(f"{base}.xml", ContentFile(datos.xml.contenido), save=False)
        if datos.xml_documento:
            documento.xml_factura_archivo.save(
                f"{base}-documento.xml", ContentFile(datos.xml_documento), save=False,
            )
        if datos.pdf is not None:
            documento.pdf_archivo.save(f"{base}.pdf", ContentFile(datos.pdf.contenido), save=False)
    except (BotoCoreError, ClientError) as error:
        _borrar_archivos(documento)
        raise ErrorTransitorio(f"No se pudo guardar el documento en B2: {error}.")
    try:
        with transaction.atomic():
            documento.save()
    except IntegrityError:
        # Otro proceso guardó el mismo CUFE entre la consulta y aquí.
        _borrar_archivos(documento)
        return False
    return True


def _nombre_archivo(datos):
    """``<número>-<8 del CUFE>``, solo con caracteres seguros para el bucket."""
    numero = re.sub(r"[^A-Za-z0-9_-]", "", datos.numero) or "documento"
    return f"{numero}-{datos.cufe_cude[:8]}"


def _borrar_archivos(documento):
    for campo in (documento.xml_archivo, documento.xml_factura_archivo, documento.pdf_archivo):
        if campo:
            try:
                campo.delete(save=False)
            except Exception:
                logger.exception("recepcion.archivo_no_borrado %s", campos(nombre=campo.name))


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
