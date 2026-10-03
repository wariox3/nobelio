"""Procesamiento de un correo recibido: lo que hace la tarea ``procesar_correo``.

Descarga el MIME de R2, guarda el asunto y el Message-ID, reconoce la
confirmación de reenvío de Gmail y registra los documentos electrónicos que
trae (``extraccion``), con todos sus archivos como ``Adjunto``:

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
from django.db import IntegrityError, transaction
from django.db.models import F

from apps.catalogos.models import Moneda
from apps.documentos.models import DocumentoTipo
from apps.emisores.models import Emisor
from apps.nucleo.registro import campos
from apps.recepcion import adjuntos, extraccion, r2
from apps.recepcion.models import Adjunto, Correo, Documento

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
    """Guarda los documentos y todos los adjuntos del correo, y deja su estado.

    Todo o nada: documentos y adjuntos van en una transacción, y si algo falla
    se borra de B2 lo que alcanzó a subir. Así un reintento empieza limpio y no
    duplica adjuntos.
    """
    try:
        extraido = extraccion.extraer(mensaje)
    except extraccion.ContenidoExcesivo as error:
        raise ErrorPermanente(str(error))
    subidos = []
    try:
        with transaction.atomic():
            conteo = _guardar_todo(correo, extraido, subidos)
    except (BotoCoreError, ClientError) as error:
        adjuntos.borrar_subidos(subidos)
        raise ErrorTransitorio(f"No se pudieron guardar los adjuntos en B2: {error}.")
    except Exception:
        adjuntos.borrar_subidos(subidos)
        raise
    nuevos, repetidos, desconocidos = conteo
    if not extraido.documentos:
        correo.estado = Correo.Estado.SIN_DOCUMENTOS
    elif nuevos or repetidos:
        correo.estado = Correo.Estado.PROCESADO
    else:
        correo.estado = Correo.Estado.EMPRESA_DESCONOCIDA
    logger.info("recepcion.documentos %s", campos(
        correo=correo.pk, nuevos=nuevos, repetidos=repetidos,
        desconocidos=desconocidos, adjuntos=len(extraido.archivos),
    ))


def _guardar_todo(correo, extraido, subidos):
    """Crea los documentos nuevos y un ``Adjunto`` por cada archivo del correo.

    Los archivos de un documento nuevo quedan con su rol (``xml``, ``pdf``) y
    el documento; los demás —también los de un documento repetido o de un
    receptor desconocido— como ``otro``.
    """
    nuevos = repetidos = desconocidos = 0
    roles = {}  # id(archivo) -> (rol, documento)
    for datos in extraido.documentos:
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
        documento = _crear_documento(datos, emisor, correo)
        if documento is None:
            repetidos += 1
            continue
        nuevos += 1
        roles[id(datos.xml)] = (Adjunto.Rol.XML, documento)
        if datos.pdf is not None:
            roles[id(datos.pdf)] = (Adjunto.Rol.PDF, documento)
        if datos.xml_documento:
            adjuntos.crear(
                correo, datos.xml_documento, nombre=_nombre_documento(datos),
                rol=Adjunto.Rol.XML_DOCUMENTO, documento=documento, subidos=subidos,
            )
    for archivo in extraido.archivos:
        rol, documento = roles.get(id(archivo), (Adjunto.Rol.OTRO, None))
        adjuntos.crear(
            correo, archivo.contenido, nombre=archivo.nombre, rol=rol,
            documento=documento, subidos=subidos,
        )
    return nuevos, repetidos, desconocidos


def _crear_documento(datos, emisor, correo):
    """El documento nuevo, o ``None`` si el CUFE ya existía (se ignora)."""
    if Documento.objects.filter(cufe_cude=datos.cufe_cude).exists():
        return None
    try:
        with transaction.atomic():
            return Documento.objects.create(
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
                moneda=(
                    Moneda.objects.filter(codigo=datos.moneda).first() if datos.moneda else None
                ),
                emisor=emisor,
                correo=correo,
            )
    except IntegrityError:
        # Otro proceso guardó el mismo CUFE entre la consulta y aquí.
        return None


def _nombre_documento(datos):
    """El nombre del XML extraído del AttachedDocument: no traía uno propio."""
    numero = re.sub(r"[^A-Za-z0-9_-]", "", datos.numero) or "documento"
    return f"{numero}.xml"


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
