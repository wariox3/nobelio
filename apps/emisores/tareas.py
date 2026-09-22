"""Tareas de Celery de los webhooks del emisor.

Los avisos se mandan una sola vez, sin reintentos (ver ``WebhookAviso``); lo que
se pierde se recupera con `respuesta-validado/` del documento. Las dos pueden
correr dos veces (``acks_late``) sin efecto doble: releen el estado antes de
mandar.
"""
from celery import shared_task


@shared_task
def responder_validado(documento_id):
    """Avisa la validación de un documento recién aceptado y, con un 200, la marca."""
    from apps.emisores.servicios import webhooks

    webhooks.responder_validado_sin_fallar(documento_id)


@shared_task
def enviar_avisos(ids):
    """Manda los avisos ``ids`` que sigan pendientes (la notificación)."""
    from apps.emisores.servicios import webhooks

    webhooks.enviar_pendientes(ids)
