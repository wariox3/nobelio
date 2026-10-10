"""Eventos RADIAN del emisor como adquiriente: 030, 031, 032 y 033.

Dos pasos, como la emisión de documentos:

1. ``solicitar`` valida que el evento quepa (tipo de documento, orden, plazos,
   exclusiones), le da su número y lo crea ``pendiente``. Encola el envío.
2. ``enviar`` (en la tarea ``enviar_evento``) genera el XML, lo firma con el
   certificado del emisor, lo manda con ``SendEventUpdateStatus`` y guarda la
   respuesta. Si la DIAN lo registra, actualiza el resumen del documento.

Las reglas son las de ``docs/recepcion.md`` («Eventos RADIAN»):

- Solo sobre facturas de venta que la DIAN tiene como válidas
  (``verificacion_estado = valido``) y que son **a crédito**: la DIAN rechaza
  los eventos sobre una de contado (regla LGC62, confirmado en producción el
  2026-10-09).
- Orden: 030 → 032 → 033 o 031. El 033 y el 031 se excluyen entre sí y van
  dentro de los 3 días hábiles siguientes al 032. ⚠ El plazo del 031 sale del
  Código de Comercio: el anexo RADIAN no trae regla propia.
- Un evento no se repite: cuenta el que está pendiente, registrado o en
  error. Uno rechazado no, y se puede pedir otro.

Un envío que falla (certificado, red, la DIAN no respondió) queda en
``error`` y no se reintenta solo: puede que la DIAN sí lo haya registrado. Se
reenvía con ``POST /api/recepcion/evento/{id}/enviar/``, con el mismo XML y el
mismo CUDE. ⚠ Si la DIAN ya lo tenía, lo rechazará como repetido; conciliarlo
con ``GetStatusEvent`` queda para cuando se conozca su respuesta real (R3).
"""
import logging
from datetime import datetime

import requests
from botocore.exceptions import BotoCoreError, ClientError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from lxml import etree

from apps.catalogos.calendario import FestivosNoCargados, sumar_dias_habiles
from apps.catalogos.models import EventoRadian, FormaPago
from apps.dian import firma, soap
from apps.dian.servicios import (
    ErrorEmision, construir_cliente_emisor, construir_firmador_emisor,
)
from apps.dian.ubl import evento as ubl_evento
from apps.documentos.models import DocumentoTipo
from apps.emisores.models import Emisor, SoftwareDian
from apps.emisores.servicios import motivo_no_puede_emitir
from apps.nucleo.colas import encolar
from apps.nucleo.models import Ambiente
from apps.nucleo.registro import campos
from apps.recepcion import extraccion
from apps.recepcion.models import (
    Adjunto, EstadoEvento, EstadoRadian, EstadoVerificacion, Evento,
)

logger = logging.getLogger(__name__)

ACUSE, RECLAMO, RECIBO, ACEPTACION = (
    ubl_evento.ACUSE, ubl_evento.RECLAMO, ubl_evento.RECIBO, ubl_evento.ACEPTACION,
)
# El prefijo del número, por código. Los de los ejemplos oficiales, salvo el
# 032, cuyo ejemplo no trae ninguno.
PREFIJOS = {ACUSE: "ACR", RECLAMO: "REC", RECIBO: "RBS", ACEPTACION: "ACE"}
# Lo que el evento registrado dice de la factura (`Documento.radian_estado`).
RESUMEN = {
    ACUSE: EstadoRadian.ACUSE,
    RECIBO: EstadoRadian.RECIBO,
    ACEPTACION: EstadoRadian.ACEPTADA,
    RECLAMO: EstadoRadian.RECLAMADA,
}
NOMBRES = {
    ACUSE: "el acuse de recibo (030)",
    RECLAMO: "el reclamo (031)",
    RECIBO: "el recibo del bien o servicio (032)",
    ACEPTACION: "la aceptación expresa (033)",
}
# El 033 y el 031 van dentro de los 3 días hábiles siguientes al 032 (DC24c).
PLAZO_DIAS_HABILES = 3
# cac:PaymentMeans/cbc:ID de una factura de contado (FormasPago-2.1.gc).
CONTADO = "1"
# Los que cuentan para el orden, las exclusiones y las repeticiones.
VIGENTES = (EstadoEvento.PENDIENTE, EstadoEvento.REGISTRADO, EstadoEvento.ERROR)
LARGO_CODIGO = 10
CAMPOS_PERSONA = (
    "tipo_identificacion", "numero_identificacion", "nombres", "apellidos", "cargo", "area",
)


class EventoInvalido(Exception):
    """El evento no se puede pedir: el mensaje dice por qué."""


class ErrorTransitorio(Exception):
    """No se pudo enviar, pero puede funcionar más tarde."""


# ===========================================================================
# Solicitar
# ===========================================================================
def solicitar(documento, codigo, *, usuario=None, persona=None, concepto_reclamo=None):
    """Crea el evento ``codigo`` sobre ``documento`` y encola su envío.

    ``persona`` es un dict con ``CAMPOS_PERSONA`` (``tipo_identificacion`` es
    un ``TipoIdentificacion``); sin ella, el 030 y el 032 salen con la del
    emisor. ``concepto_reclamo`` es un ``ConceptoReclamo``, solo en el 031.
    Lanza ``EventoInvalido`` si el evento no cabe.
    """
    emisor = documento.emisor
    _validar_documento(documento)
    tipo = EventoRadian.objects.filter(codigo=codigo, activo=True).first()
    if tipo is None:
        raise EventoInvalido(f"El evento {codigo} no existe o no está activo.")
    if codigo in ubl_evento.EVENTOS_CON_PERSONA:
        persona = persona or _persona_del_emisor(emisor)
        if persona is None:
            raise EventoInvalido(
                f"El evento {codigo} lleva la persona que recibe: mándala en la "
                "petición o configúrala en el emisor."
            )
    else:
        persona = None
    if codigo == RECLAMO and concepto_reclamo is None:
        raise EventoInvalido("El reclamo (031) lleva su concepto.")
    if codigo != RECLAMO:
        concepto_reclamo = None
    if motivo := motivo_no_puede_emitir(emisor):
        raise EventoInvalido(motivo)
    if not emisor.softwares.filter(tipo=SoftwareDian.Tipo.FACTURACION).exists():
        raise EventoInvalido("El emisor no tiene registrado un software DIAN de facturación.")

    with transaction.atomic():
        # El candado del emisor serializa la numeración y las validaciones de
        # orden: dos peticiones a la vez no sacan el mismo consecutivo ni
        # registran dos veces el mismo evento.
        Emisor.objects.select_for_update().get(pk=emisor.pk)
        _validar_orden(documento, codigo)
        consecutivo = (
            Evento.objects.filter(emisor=emisor, evento_radian=tipo)
            .aggregate(ultimo=Max("consecutivo"))["ultimo"] or 0
        ) + 1
        evento = Evento.objects.create(
            documento=documento,
            emisor=emisor,
            evento_radian=tipo,
            concepto_reclamo=concepto_reclamo,
            usuario=usuario,
            consecutivo=consecutivo,
            numero=f"{PREFIJOS[codigo]}{consecutivo}",
            ambiente=documento.ambiente or Ambiente.PRODUCCION,
            **{f"persona_{campo}": valor for campo, valor in (persona or {}).items()},
        )
        from apps.recepcion.tareas import enviar_evento

        encolar(enviar_evento, str(evento.pk))
    logger.info("recepcion.evento_solicitado %s", campos(
        evento=evento.pk, documento=documento.pk, codigo=codigo, numero=evento.numero,
        usuario=getattr(usuario, "pk", None),
    ))
    return evento


def acuse_automatico(documento):
    """El 030 de una factura recién verificada, si el emisor lo tiene encendido.

    Lo llama la verificación cuando la DIAN dice que la factura es válida.
    No falla nunca: lo que impide el acuse (sin persona configurada, sin
    certificado, ya hay uno) se deja en el log y la factura sigue su camino;
    el acuse se puede pedir a mano después. Devuelve el evento o ``None``.
    """
    emisor = documento.emisor
    if not emisor.acuse_automatico:
        return None
    if documento.documento_tipo.codigo != DocumentoTipo.Codigo.FACTURA_VENTA:
        return None
    if Evento.objects.filter(
        documento=documento, evento_radian__codigo=ACUSE, estado__in=VIGENTES,
    ).exists():
        return None
    try:
        return solicitar(documento, ACUSE)
    except EventoInvalido as error:
        logger.info("recepcion.acuse_omitido %s", campos(
            documento=documento.pk, emisor=emisor.pk, motivo=str(error),
        ))
        return None


def _validar_documento(documento):
    if documento.documento_tipo.codigo != DocumentoTipo.Codigo.FACTURA_VENTA:
        raise EventoInvalido("Los eventos RADIAN son solo sobre facturas de venta.")
    if documento.verificacion_estado != EstadoVerificacion.VALIDO:
        raise EventoInvalido(
            "La DIAN no tiene verificada la factura como válida "
            f"(verificación: {documento.get_verificacion_estado_display().lower()}). "
            "Verifícala antes con verificar/."
        )
    if _forma_pago(documento) == CONTADO:
        raise EventoInvalido(
            "La factura es de contado: la DIAN solo admite eventos RADIAN sobre "
            "facturas a crédito (regla LGC62)."
        )
    if documento.fecha_emision > _hoy():
        # DC24a: el 030 no se firma antes de la fecha de la factura; los
        # demás van después del 030.
        raise EventoInvalido("La factura tiene fecha futura: todavía no admite eventos.")


def _forma_pago(documento):
    """El código de la forma de pago, o ``""`` si no se sabe.

    Los documentos de antes no la tienen guardada: se lee de su XML en B2 y se
    guarda, una sola vez. Si no se puede leer, no se sabe, y el evento sigue:
    que decida la DIAN.
    """
    if documento.forma_pago_id is None:
        codigo = _forma_pago_del_xml(documento)
        documento.forma_pago = FormaPago.objects.filter(codigo=codigo).first() if codigo else None
        if documento.forma_pago is not None:
            documento.save(update_fields=["forma_pago", "actualizado_en"])
    return documento.forma_pago.codigo if documento.forma_pago else ""


def _forma_pago_del_xml(documento):
    for rol in (Adjunto.Rol.XML_DOCUMENTO, Adjunto.Rol.XML):
        adjunto = documento.adjuntos.filter(rol=rol).first()
        if adjunto is None:
            continue
        try:
            with adjunto.archivo.open("rb") as fh:
                extraido = extraccion.extraer_archivo(adjunto.nombre, fh.read())
        except (BotoCoreError, ClientError, extraccion.ContenidoExcesivo) as error:
            logger.warning("recepcion.forma_pago_ilegible %s", campos(
                documento=documento.pk, error=str(error),
            ))
            return ""
        for datos in extraido.documentos:
            if datos.cufe_cude == documento.cufe_cude:
                return datos.forma_pago
    return ""


def _validar_orden(documento, codigo):
    """Orden, exclusiones, repeticiones y plazos, contra los eventos vigentes."""
    vigentes = {}
    for evento in Evento.objects.filter(
        documento=documento, estado__in=VIGENTES,
    ).select_related("evento_radian"):
        vigentes.setdefault(evento.evento_radian.codigo, evento)

    if repetido := vigentes.get(codigo):
        raise EventoInvalido(
            f"La factura ya tiene {NOMBRES[codigo]}: {repetido.numero}, "
            f"{repetido.get_estado_display().lower()}."
        )
    if codigo == RECIBO:
        _exigir_registrado(vigentes, ACUSE, codigo)
    if codigo in (ACEPTACION, RECLAMO):
        otro = RECLAMO if codigo == ACEPTACION else ACEPTACION
        if otro in vigentes:
            raise EventoInvalido(
                f"La factura ya tiene {NOMBRES[otro]}, que excluye {NOMBRES[codigo]}."
            )
        recibo = _exigir_registrado(vigentes, RECIBO, codigo)
        _validar_plazo(recibo, codigo)


def _exigir_registrado(vigentes, previo, codigo):
    evento = vigentes.get(previo)
    if evento is None or evento.estado != EstadoEvento.REGISTRADO:
        raise EventoInvalido(
            f"{NOMBRES[codigo].capitalize()} va después {_de(NOMBRES[previo])} "
            "registrado en RADIAN."
        )
    return evento


def _de(nombre):
    """``de`` + ``nombre``, con la contracción: «del acuse», «de la aceptación»."""
    return f"del {nombre[3:]}" if nombre.startswith("el ") else f"de {nombre}"


def _validar_plazo(recibo, codigo):
    try:
        limite = sumar_dias_habiles(recibo.fecha, PLAZO_DIAS_HABILES)
    except FestivosNoCargados as error:
        raise EventoInvalido(f"No se puede contar el plazo: {error}")
    if _hoy() > limite:
        raise EventoInvalido(
            f"Venció el plazo para {NOMBRES[codigo]}: eran {PLAZO_DIAS_HABILES} "
            f"días hábiles desde el 032 ({recibo.fecha:%Y-%m-%d}), hasta el "
            f"{limite:%Y-%m-%d}."
        )


def _hoy():
    """La fecha de hoy en Colombia, contra la que se cuentan los plazos."""
    return timezone.localdate()


def _persona_del_emisor(emisor):
    """La persona que recibe configurada en el emisor, o ``None`` si falta."""
    persona = {campo: getattr(emisor, f"recibe_{campo}") for campo in CAMPOS_PERSONA}
    obligatorios = ("tipo_identificacion", "numero_identificacion", "nombres", "apellidos")
    if not all(persona[campo] for campo in obligatorios):
        return None
    return persona


# ===========================================================================
# Enviar
# ===========================================================================
def enviar(evento, *, cliente=None, firmador=None):
    """Genera, firma y envía ``evento``, y guarda lo que respondió la DIAN.

    Solo los ``pendiente`` y los ``error``; los demás ya terminaron. El XML se
    genera una vez: un reenvío manda el mismo, con el mismo CUDE. Lanza
    ``ErrorTransitorio`` si no se pudo enviar; en ese caso el evento queda
    como estaba. ``cliente`` y ``firmador`` son para las pruebas.
    """
    if evento.estado not in (EstadoEvento.PENDIENTE, EstadoEvento.ERROR):
        return evento
    Evento.objects.filter(pk=evento.pk).update(intentos=evento.intentos + 1)
    evento.intentos += 1
    emisor = evento.emisor
    try:
        if not evento.xml_archivo:
            _generar(evento, firmador or construir_firmador_emisor(emisor))
        xml_firmado = _leer(evento.xml_archivo)
        cliente = cliente or construir_cliente_emisor(emisor, evento.ambiente)
    except (BotoCoreError, ClientError) as error:
        raise ErrorTransitorio(f"No se pudo leer el certificado o el XML en B2: {error}.")
    except ErrorEmision as error:
        # El certificado venció o falta: se arregla cargando otro y reenviando.
        raise ErrorTransitorio(str(error))
    try:
        respuesta = cliente.enviar_evento(xml_firmado, _nombre_archivo(evento))
    except (requests.RequestException, etree.XMLSyntaxError) as error:
        raise ErrorTransitorio(f"La DIAN no respondió: {error}.")
    return _guardar_respuesta(evento, respuesta)


def marcar_error(evento, detalle):
    """Deja el evento en ``error`` con el motivo, para reenviarlo a mano."""
    evento.estado = EstadoEvento.ERROR
    evento.respuesta_descripcion = detalle
    evento.save(update_fields=["estado", "respuesta_descripcion", "actualizado_en"])
    logger.warning("recepcion.evento_error %s", campos(evento=evento.pk, detalle=detalle))
    return evento


def _generar(evento, firmador):
    """El XML firmado del evento, con su fecha, hora y CUDE."""
    documento, emisor = evento.documento, evento.emisor
    software = emisor.softwares.get(tipo=SoftwareDian.Tipo.FACTURACION)
    ahora = datetime.now(firma.TZ_COLOMBIA)
    codigo = evento.evento_radian.codigo
    datos = ubl_evento.Evento(
        codigo=codigo,
        descripcion=evento.evento_radian.nombre,
        numero=evento.numero,
        fecha=ahora.date(),
        hora=ahora.time().replace(microsecond=0),
        emisor=ubl_evento.Parte(
            emisor.razon_social, emisor.numero_identificacion,
            tipo_identificacion=emisor.tipo_identificacion.codigo,
            tipo_organizacion=(
                emisor.tipo_organizacion.codigo if emisor.tipo_organizacion
                else ubl_evento.PERSONA_JURIDICA
            ),
        ),
        receptor=ubl_evento.Parte(
            documento.proveedor_razon_social, documento.proveedor_numero_identificacion,
            tipo_identificacion=documento.proveedor_tipo_identificacion or ubl_evento.SCHEME_NIT,
            tipo_organizacion=(
                documento.proveedor_tipo_organizacion or ubl_evento.PERSONA_JURIDICA
            ),
        ),
        factura_numero=documento.numero,
        factura_cufe=documento.cufe_cude,
        persona=_persona(evento),
        concepto_reclamo=(
            (evento.concepto_reclamo.codigo, evento.concepto_reclamo.nombre)
            if evento.concepto_reclamo else None
        ),
    )
    constructor = ubl_evento.ConstructorEvento(datos, software=software, ambiente=evento.ambiente)
    firmado = firmador.firmar(constructor.generar_xml())
    evento.cude = constructor.cude
    evento.fecha, evento.hora = datos.fecha, datos.hora
    evento.xml_archivo.save(_nombre_archivo(evento), ContentFile(firmado), save=False)
    evento.save(update_fields=["cude", "fecha", "hora", "xml_archivo", "actualizado_en"])


def _persona(evento):
    if not evento.persona_numero_identificacion:
        return None
    return ubl_evento.Persona(
        evento.persona_tipo_identificacion.codigo,
        evento.persona_numero_identificacion,
        evento.persona_nombres,
        evento.persona_apellidos,
        evento.persona_cargo,
        evento.persona_area,
    )


def _guardar_respuesta(evento, respuesta):
    evento.estado = EstadoEvento.REGISTRADO if respuesta.es_valido else EstadoEvento.RECHAZADO
    evento.respuesta_codigo = respuesta.codigo_estado[:LARGO_CODIGO]
    evento.respuesta_descripcion = " ".join(
        filter(None, [respuesta.descripcion_estado, *respuesta.errores])
    )
    evento.enviado_en = timezone.now()
    campos_guardados = [
        "estado", "respuesta_codigo", "respuesta_descripcion", "enviado_en", "actualizado_en",
    ]
    application_response = (
        soap.extraer_application_response(respuesta.xml_crudo) if respuesta.xml_crudo else b""
    )
    if application_response:
        evento.respuesta_archivo.save(
            f"{_nombre_archivo(evento)[:-4]}-dian.xml", ContentFile(application_response),
            save=False,
        )
        campos_guardados.append("respuesta_archivo")
    with transaction.atomic():
        evento.save(update_fields=campos_guardados)
        if evento.estado == EstadoEvento.REGISTRADO:
            documento = evento.documento
            documento.radian_estado = RESUMEN[evento.evento_radian.codigo]
            documento.save(update_fields=["radian_estado", "actualizado_en"])
    logger.info("recepcion.evento_enviado %s", campos(
        evento=evento.pk, estado=evento.estado, codigo=evento.respuesta_codigo,
    ))
    return evento


def _nombre_archivo(evento):
    """``ar`` + NIT del emisor a 10 + código del evento + consecutivo a 8."""
    return (
        f"ar{evento.emisor.numero_identificacion.zfill(10)}"
        f"{evento.evento_radian.codigo}{evento.consecutivo:08d}.xml"
    )


def _leer(archivo):
    with archivo.open("rb") as fh:
        return fh.read()
