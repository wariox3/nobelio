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

``reprocesar`` vuelve a pasar por aquí un correo que se quedó en el camino: lo
usa el comando ``reprocesar_correos``.

``cargar`` registra un ZIP o XML que un usuario sube a mano: la misma
extracción y el mismo guardado, pero en el request y para un emisor fijo.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from botocore.exceptions import BotoCoreError, ClientError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.catalogos.models import FormaPago, Moneda
from apps.documentos.models import DocumentoTipo
from apps.emisores.models import Emisor
from apps.nucleo.colas import encolar
from apps.nucleo.registro import campos
from apps.recepcion import adjuntos, extraccion, r2
from apps.recepcion.models import Adjunto, Correo, Documento
from apps.recepcion.tareas import verificar_documento

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

# Lo que se puede reprocesar: lo que no terminó y lo que llegó antes de que
# registraran a su emisor. Lo demás ya quedó como debía.
ESTADOS_REPROCESABLES = ESTADOS_PROCESABLES | {Correo.Estado.EMPRESA_DESCONOCIDA}
# Un pendiente más reciente puede estar todavía en la cola: reprocesarlo a la
# vez que la tarea duplicaría sus adjuntos.
ESPERA_PENDIENTE = timedelta(minutes=30)


class ErrorTransitorio(Exception):
    """Un fallo que puede arreglarse solo (R2 caído, red): se reintenta."""


class ErrorPermanente(Exception):
    """Un fallo que no se arregla reintentando: el correo queda en ``error``."""


class CargaInvalida(Exception):
    """El archivo cargado no trae nada que registrar. El mensaje es para el
    usuario."""


@dataclass
class Rechazo:
    datos: object
    motivo: str


@dataclass
class Resultado:
    """Qué pasó con cada documento de una entrada."""

    creados: list = field(default_factory=list)
    # El documento que ya existía, o `None` si no es del emisor de la carga.
    repetidos: list = field(default_factory=list)
    # Los de un receptor que no es ningún emisor (correo) o que no es el
    # emisor elegido (carga).
    rechazados: list = field(default_factory=list)


class _SinNuevos(Exception):
    """Deshace una carga que no creó nada: no se guarda ni la fila ni sus
    adjuntos."""

    def __init__(self, resultado):
        self.resultado = resultado


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


def emisor_del_alias(alias):
    """El emisor cuyo NIT es el alias, o ``None``.

    El buzón de cada emisor es su NIT sin DV: ``901192048@recepcion.rededoc.co``.
    Si el alias no es un NIT registrado, el correo queda sin emisor; se guarda
    igual para reprocesarlo cuando lo den de alta.
    """
    if not alias.isdigit():
        return None
    return Emisor.objects.filter(numero_identificacion=alias).first()


class NoReprocesable(Exception):
    """El correo no está en un estado que se pueda reprocesar."""


def reprocesables():
    """Los correos que ``reprocesar_correos --todos`` vuelve a procesar."""
    limite = timezone.now() - ESPERA_PENDIENTE
    return Correo.objects.filter(
        estado__in=ESTADOS_REPROCESABLES,
    ).exclude(
        estado=Correo.Estado.PENDIENTE, recibido_en__gt=limite,
    ).order_by("recibido_en", "id")


def reprocesar(correo_id):
    """Procesa de nuevo el correo ``correo_id``, en este proceso.

    Antes borra sus adjuntos (los de un correo sin documentos, que son todos
    ``otro``) para no duplicarlos, lo deja ``pendiente`` y, si sigue sin
    emisor, lo busca otra vez por el alias. Devuelve el correo ya procesado.

    Lanza ``NoReprocesable`` si el correo ya terminó o tiene documentos, y deja
    subir ``ErrorTransitorio``: el correo queda ``pendiente``, sin adjuntos, y
    se puede reprocesar otra vez.
    """
    correo = Correo.objects.get(pk=correo_id)
    if correo.estado not in ESTADOS_REPROCESABLES:
        raise NoReprocesable(f"El correo {correo.pk} está {correo.get_estado_display().lower()}.")
    if correo.documentos.exists():
        raise NoReprocesable(f"El correo {correo.pk} ya tiene documentos.")
    adjuntos.vaciar_correo(correo)
    correo.estado = Correo.Estado.PENDIENTE
    correo.emisor = correo.emisor or emisor_del_alias(correo.alias)
    correo.save(update_fields=["estado", "emisor"])
    procesar(correo.pk)
    correo.refresh_from_db()
    return correo


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
            resultado = _guardar_todo(correo, extraido, subidos)
    except (BotoCoreError, ClientError) as error:
        adjuntos.borrar_subidos(subidos)
        raise ErrorTransitorio(f"No se pudieron guardar los adjuntos en B2: {error}.")
    except Exception:
        adjuntos.borrar_subidos(subidos)
        raise
    if not extraido.documentos:
        correo.estado = Correo.Estado.SIN_DOCUMENTOS
    elif resultado.creados or resultado.repetidos:
        correo.estado = Correo.Estado.PROCESADO
    else:
        correo.estado = Correo.Estado.EMPRESA_DESCONOCIDA
    logger.info("recepcion.documentos %s", campos(
        correo=correo.pk, nuevos=len(resultado.creados),
        repetidos=len(resultado.repetidos), desconocidos=len(resultado.rechazados),
        adjuntos=len(extraido.archivos),
    ))


def _guardar_todo(correo, extraido, subidos, emisor_fijo=None):
    """Crea los documentos nuevos y un ``Adjunto`` por cada archivo del correo.

    Los archivos de un documento nuevo quedan con su rol (``xml``, ``pdf``) y
    el documento; los demás —también los de un documento repetido o de un
    receptor desconocido— como ``otro``.

    Con ``emisor_fijo`` (una carga) cada documento tiene que ser de ese emisor:
    no se busca el emisor por el NIT receptor, porque quien sube el archivo
    solo puede registrar documentos de los emisores que alcanza.
    """
    resultado = Resultado()
    roles = {}  # id(archivo) -> (rol, documento)
    for datos in extraido.documentos:
        receptor = datos.receptor_numero_identificacion
        if emisor_fijo is not None:
            if receptor != emisor_fijo.numero_identificacion:
                resultado.rechazados.append(Rechazo(datos, (
                    f"El receptor del documento (NIT {receptor}) no es el "
                    f"emisor seleccionado (NIT {emisor_fijo.numero_identificacion})."
                )))
                continue
            emisor = emisor_fijo
        else:
            emisor = Emisor.objects.filter(numero_identificacion=receptor).first()
            if emisor is None:
                resultado.rechazados.append(Rechazo(
                    datos, f"El NIT receptor {receptor} no es de ningún emisor.",
                ))
                continue
        if correo.emisor_id is None:
            # Llegó a un buzón que no es un NIT registrado, pero el XML dice
            # de quién es.
            correo.emisor = emisor
        documento = _crear_documento(datos, emisor, correo)
        if documento is None:
            resultado.repetidos.append(Documento.objects.filter(
                cufe_cude=datos.cufe_cude, emisor=emisor,
            ).first())
            continue
        resultado.creados.append(documento)
        # Al confirmar la transacción: si la carga se deshace, no se verifica
        # un documento que ya no existe.
        encolar(verificar_documento, str(documento.pk))
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
    return resultado


def cargar(emisor, usuario, nombre, contenido):
    """Registra los documentos de un ZIP o XML que un usuario subió a mano.

    Todo en el request, con las reglas del correo salvo una: los documentos
    tienen que ser de ``emisor`` (el NIT receptor del XML) y los de otro se
    rechazan. Devuelve el ``Resultado``.

    La carga (una fila de ``Correo`` con ``origen = carga``) y sus adjuntos
    solo se guardan si se creó al menos un documento. Si todo estaba repetido
    no se guarda nada y se devuelve igual, para que quien sube vea cuáles
    eran. Lanza ``CargaInvalida`` si el archivo no trae ningún documento o
    todos son de otro receptor, y deja subir los errores de B2.
    """
    try:
        extraido = extraccion.extraer_archivo(nombre, contenido)
    except extraccion.ContenidoExcesivo as error:
        raise CargaInvalida(str(error))
    if not extraido.documentos:
        raise CargaInvalida(
            "El archivo no trae ninguna factura ni nota electrónica: no se "
            "encontró un XML de la DIAN que se pudiera leer."
        )
    subidos = []
    try:
        with transaction.atomic():
            carga = Correo.objects.create(
                origen=Correo.Origen.CARGA,
                usuario=usuario,
                emisor=emisor,
                alias=emisor.numero_identificacion,
                asunto=adjuntos.nombre_seguro(nombre),
                estado=Correo.Estado.PROCESADO,
            )
            resultado = _guardar_todo(carga, extraido, subidos, emisor_fijo=emisor)
            if not resultado.creados:
                raise _SinNuevos(resultado)
    except _SinNuevos as sin_nuevos:
        adjuntos.borrar_subidos(subidos)
        resultado = sin_nuevos.resultado
        if not resultado.repetidos:
            raise CargaInvalida(" ".join(r.motivo for r in resultado.rechazados))
        return resultado
    except Exception:
        adjuntos.borrar_subidos(subidos)
        raise
    logger.info("recepcion.carga %s", campos(
        carga=carga.pk, emisor=emisor.pk, usuario=getattr(usuario, "pk", None),
        nuevos=len(resultado.creados), repetidos=len(resultado.repetidos),
        rechazados=len(resultado.rechazados),
    ))
    return resultado


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
                proveedor_tipo_identificacion=datos.proveedor_tipo_identificacion,
                proveedor_tipo_organizacion=datos.proveedor_tipo_organizacion,
                proveedor_razon_social=datos.proveedor_razon_social,
                receptor_numero_identificacion=datos.receptor_numero_identificacion,
                valor_bruto=datos.valor_bruto,
                total_impuestos=datos.total_impuestos,
                total_a_pagar=datos.total_a_pagar,
                validacion_codigo=datos.validacion_codigo,
                fecha_validacion=datos.fecha_validacion,
                ambiente=datos.ambiente,
                documento_tipo=DocumentoTipo.objects.get(codigo=datos.tipo),
                forma_pago=(
                    FormaPago.objects.filter(codigo=datos.forma_pago).first()
                    if datos.forma_pago else None
                ),
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
