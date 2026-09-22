"""Tareas de Celery de los documentos."""
import logging

import requests
from celery import shared_task

from apps.nucleo.registro import campos

logger = logging.getLogger(__name__)


@shared_task
def emitir_documento(documento_id):
    """Lleva a su estado final ante la DIAN el documento que se acaba de crear.

    La encola la creación (``DOCUMENTOS_EMITIR_AL_CREAR``). Hace lo mismo que
    `emitir/` —la misma función, con el mismo bloqueo—, así que una llamada a
    mano mientras la tarea corre no envía dos veces.

    **Sin reintentos**, por decisión expresa: si la DIAN no responde, el
    documento se queda donde quedó —`firmado`, o `enviado` sin veredicto— y se
    termina con `emitir/`. Tampoco se reintenta lo que no se puede emitir (sin
    certificado, rechazado, ya aceptado): se deja en el log.

    Puede correr dos veces (``acks_late``): la segunda encuentra el documento
    en otro estado y hace lo que toca desde ahí, o nada.
    """
    # Los imports van aquí: el worker carga las tareas al arrancar, y el
    # pipeline DIAN arrastra firma, XML y SOAP, que no necesita hasta la
    # primera tarea.
    from apps.dian import servicios
    from apps.documentos.models import Documento

    documento = Documento.objects.filter(pk=documento_id).first()
    if documento is None:
        # Se borró entre la creación y la tarea.
        return
    try:
        servicios.emitir(documento)
    except servicios.ErrorEmision as exc:
        logger.warning("tarea.emitir_no_emitible %s", campos(documento=documento_id, motivo=str(exc)))
    except requests.RequestException as exc:
        logger.warning("tarea.emitir_sin_respuesta %s", campos(documento=documento_id, motivo=str(exc)))
