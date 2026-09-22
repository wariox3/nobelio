"""Envío de los avisos de webhook, firmados con HMAC.

El contrato es el de torio, que es quien los recibe:
`torio/docs/webhook_rededoc.md`. Lo que no esté allí no forma parte de él.

El aviso no puede tumbar lo que lo dispara. El envío va en una tarea de Celery
(``apps/emisores/tareas.py``) que se encola al confirmarse el cambio, y ningún
error suyo sale de aquí: si torio no responde, el documento queda validado
igual y el aviso, `fallido`, con el motivo.

- **Notificación**: los avisos se crean `pendiente` en la transacción del
  cambio y la tarea los manda (``avisar``).
- **Validación**: la tarea llama a ``responder_validado``, que los crea, los
  manda y, con un 200 —o si no hay a quién avisar—, marca el documento con
  `respuesta_validado`. Lo que se quede sin marcar —el broker no respondió, el
  receptor tampoco— se recupera con el endpoint `respuesta-validado` del
  documento, que llama a la misma función en la petición.

Cada aviso sale **una sola vez**, sin reintentos (ver ``WebhookAviso``).
"""
import hashlib
import hmac
import json
import logging
import time

import requests
from django.utils import timezone

from apps.documentos.models import Documento, DocumentoEstado
from apps.emisores.models import WebhookAviso
from apps.nucleo.colas import encolar
from apps.nucleo.registro import campos

logger = logging.getLogger(__name__)

# Lo que espera cada aviso. Corto porque `respuesta-validado/` lo manda dentro
# de la petición, y un receptor lento haría esperar a quien lo llamó; en el
# worker ocupa un proceso mientras tanto.
TIMEOUT_SEGUNDOS = 5

# Los que el contrato da por entregados. El 409 es "ya estaba validado": si
# torio aplicó la validación y su 200 se perdió en la red, el reenvío recibe
# 409, y eso es que llegó.
ENTREGADOS = {200, 409}

# Tope de lo que se guarda del `detail` de la respuesta: es para leerlo, no
# para archivar lo que devuelva el receptor.
MAXIMO_ERROR = 1000

BANDERA_POR_TIPO = {
    WebhookAviso.Tipo.VALIDACION: "estado_validado",
    WebhookAviso.Tipo.NOTIFICACION: "estado_notificado",
}

MENSAJE_SIN_SECRETO = "El webhook no tiene secreto: sin él no se puede firmar."
MENSAJE_SIN_REFERENCIA = (
    "El emisor no tiene referencia externa: el aviso no puede decir de qué "
    "cliente es."
)


def firmar(secreto: str, fecha: str, cuerpo: bytes) -> str:
    """La firma del contrato: ``v1=`` + hex(HMAC-SHA256(secreto, fecha.cuerpo)).

    La fecha entra en lo firmado para que un aviso capturado no se pueda
    reenviar después: torio rechaza una fecha a más de 5 minutos de su hora.
    """
    mensaje = fecha.encode() + b"." + cuerpo
    return "v1=" + hmac.new(secreto.encode(), mensaje, hashlib.sha256).hexdigest()


def _cliente(emisor):
    """La referencia externa, como número si lo es.

    El contrato pide un entero, pero la referencia es texto para que sirva a
    cualquier ERP; la de torio siempre son dígitos.
    """
    referencia = emisor.referencia_externa.strip()
    return int(referencia) if referencia.isdigit() else referencia


def armar_cuerpo(documento, tipo) -> str:
    """El JSON del aviso, serializado una sola vez.

    Se guarda y se manda tal cual: firmar un dict y dejar que la librería HTTP
    lo vuelva a serializar produce otros bytes, y la firma no cuadra.
    """
    aviso = {
        "tipo": tipo,
        "cliente": _cliente(documento.emisor),
        "documento": str(documento.pk),
    }
    if tipo == WebhookAviso.Tipo.VALIDACION:
        # Con zona horaria: sin ella, torio la lee como hora de Bogotá, y con
        # hora: una fecha sola la rechaza.
        aviso["fecha_validacion"] = timezone.localtime(
            documento.fecha_validacion
        ).isoformat(timespec="seconds")
        aviso["cufe"] = documento.cufe_cude
    return json.dumps(aviso)


def _crear_avisos(documento, tipo):
    """Un aviso por cada webhook del emisor que lo pide.

    Los que no se pueden mandar —webhook sin secreto, emisor sin referencia
    externa— quedan ya `fallido`, con el motivo, para que se vea por qué no
    salió nada.
    """
    webhooks = list(documento.emisor.webhooks.filter(**{BANDERA_POR_TIPO[tipo]: True}))
    if not webhooks:
        return []

    sin_referencia = not documento.emisor.referencia_externa.strip()
    cuerpo = "" if sin_referencia else armar_cuerpo(documento, tipo)
    avisos = []
    for webhook in webhooks:
        motivo = MENSAJE_SIN_REFERENCIA if sin_referencia else (
            MENSAJE_SIN_SECRETO if not webhook.secreto else ""
        )
        avisos.append(WebhookAviso.objects.create(
            webhook=webhook, documento=documento, tipo=tipo, cuerpo=cuerpo,
            estado=WebhookAviso.Estado.FALLIDO if motivo else WebhookAviso.Estado.PENDIENTE,
            error=motivo,
        ))
    return avisos


def avisar(documento, tipo):
    """Deja los avisos del cambio y encola su envío para cuando se confirme.

    Va dentro de la transacción del cambio, si la hay: los avisos se crean con
    él y se deshacen con él. Si el broker no responde se quedan `pendiente`.
    """
    from apps.emisores import tareas

    avisos = _crear_avisos(documento, tipo)
    pendientes = [a.pk for a in avisos if a.estado == WebhookAviso.Estado.PENDIENTE]
    if pendientes:
        encolar(tareas.enviar_avisos, pendientes)
    return avisos


def avisar_ya(documento, tipo):
    """Crea los avisos y los envía en el acto, esperando la respuesta.

    Para quien necesita el resultado ahora —el endpoint `respuesta-validado`
    del documento—, no para los cambios de estado: esos no pueden esperar a
    que el receptor conteste. Un fallo del envío queda en el aviso; no sale.
    """
    avisos = _crear_avisos(documento, tipo)
    for aviso in avisos:
        if aviso.estado == WebhookAviso.Estado.PENDIENTE:
            try:
                entregar(aviso)
            except Exception:
                logger.exception("webhook.aviso_error %s", campos(aviso=aviso.pk))
    return avisos


def responder_validado(documento):
    """Avisa la validación y, si alguien responde 200, la da por respondida.

    Devuelve ``(respondido, avisos)``. Sin webhooks de validación no hay a
    quién avisar y el documento se marca igual. Solo el 200 cuenta: un 409
    ("ya estaba validado") no marca, por decisión expresa.

    No comprueba el estado del documento: eso es de quien la llama.
    """
    avisos = avisar_ya(documento, WebhookAviso.Tipo.VALIDACION)
    if avisos and not any(aviso.codigo_http == 200 for aviso in avisos):
        return False, avisos
    documento.respuesta_validado = True
    documento.save(update_fields=["respuesta_validado", "actualizado_en"])
    return True, avisos


def responder_validado_al_confirmar(documento):
    """Encola ``responder_validado`` para cuando se confirme la aceptación.

    En el worker y no en la petición: el envío espera al receptor, y no puede
    ni retener la transacción de la emisión ni avisar de algo que luego se
    deshaga.
    """
    from apps.emisores import tareas

    encolar(tareas.responder_validado, str(documento.pk))


def responder_validado_sin_fallar(pk):
    """``responder_validado`` desde la tarea. Nunca lanza.

    Relee el documento: entre la aceptación y este momento otra petición pudo
    marcarlo, y avisar dos veces no sirve de nada. Sin reintentos: lo que falle
    queda en el log y en los avisos.
    """
    try:
        documento = Documento.objects.select_related("estado", "emisor").get(pk=pk)
        if (
            documento.estado.nombre != DocumentoEstado.Nombre.ACEPTADO
            or documento.respuesta_validado
        ):
            return
        responder_validado(documento)
    except Exception:
        logger.exception("webhook.respuesta_validado_error %s", campos(documento=pk))


def enviar_pendientes(ids):
    """Envía los avisos ``ids`` que sigan pendientes. Nunca lanza.

    Corre en la tarea de Celery. Que no lance es para que un aviso roto no
    impida mandar los siguientes.
    """
    for aviso in WebhookAviso.objects.filter(
        pk__in=ids, estado=WebhookAviso.Estado.PENDIENTE,
    ).select_related("webhook"):
        try:
            entregar(aviso)
        except Exception:
            logger.exception("webhook.aviso_error %s", campos(aviso=aviso.pk))


def _detalle(respuesta):
    """El `detail` de la respuesta, o su texto si no es JSON."""
    try:
        detalle = respuesta.json().get("detail", "")
    except (ValueError, AttributeError):
        detalle = respuesta.text
    return str(detalle)[:MAXIMO_ERROR]


def entregar(aviso, *, sesion=None):
    """Firma el aviso con la hora de ahora y lo manda. Deja el resultado en él.

    Se firma aquí y no al crearlo: la fecha tiene que ser la del envío, y el
    secreto, el vigente, para que una rotación valga también para lo que
    estaba por salir.
    """
    cuerpo = aviso.cuerpo.encode()
    fecha = str(int(time.time()))
    cabeceras = {
        "Content-Type": "application/json",
        "X-Rededoc-Fecha": fecha,
        "X-Rededoc-Firma": firmar(aviso.webhook.secreto, fecha, cuerpo),
    }
    http = sesion or requests
    aviso.enviado_en = timezone.now()
    try:
        # `data=` con los bytes, no `json=`: es lo que garantiza que viaja
        # exactamente lo que se firmó.
        respuesta = http.post(
            aviso.webhook.url, data=cuerpo, headers=cabeceras, timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.RequestException as exc:
        aviso.estado = WebhookAviso.Estado.FALLIDO
        aviso.codigo_http = None
        aviso.error = f"{type(exc).__name__}: {exc}"[:MAXIMO_ERROR]
    else:
        aviso.codigo_http = respuesta.status_code
        aviso.estado = (
            WebhookAviso.Estado.ENTREGADO if respuesta.status_code in ENTREGADOS
            else WebhookAviso.Estado.FALLIDO
        )
        aviso.error = "" if respuesta.status_code == 200 else _detalle(respuesta)
    aviso.save(update_fields=["estado", "codigo_http", "error", "enviado_en"])

    registro = logger.info if aviso.estado == WebhookAviso.Estado.ENTREGADO else logger.warning
    registro("webhook.aviso %s", campos(
        aviso=aviso.pk,
        webhook=aviso.webhook_id,
        documento=aviso.documento_id,
        tipo=aviso.tipo,
        estado=aviso.estado,
        codigo=aviso.codigo_http,
    ))
    return aviso
