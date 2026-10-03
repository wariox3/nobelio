"""Tareas de Celery de la recepción de correos."""
from celery import shared_task

# Reintentos ante un fallo transitorio de R2: a los 1, 2, 4, 8 y 16 minutos.
MAXIMO_REINTENTOS = 5


@shared_task(bind=True, max_retries=MAXIMO_REINTENTOS)
def procesar_correo(self, correo_id):
    """Procesa el correo recién registrado por ``POST /recepcion/inbound``.

    Puede correr dos veces (``acks_late``) sin efecto doble: lo que ya se
    procesó no se vuelve a tocar. Ante un fallo transitorio reintenta con
    espera creciente; agotados los reintentos, el correo queda en ``error``.
    """
    from apps.recepcion import procesamiento

    try:
        procesamiento.procesar(correo_id)
    except procesamiento.ErrorTransitorio as error:
        if self.request.retries >= self.max_retries:
            procesamiento.marcar_error(correo_id, str(error))
            return
        raise self.retry(exc=error, countdown=60 * 2 ** self.request.retries)
