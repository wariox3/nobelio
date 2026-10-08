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


@shared_task(bind=True, max_retries=MAXIMO_REINTENTOS)
def verificar_documento(self, documento_id):
    """Verifica contra la DIAN un documento recién registrado.

    La encola el procesamiento al crear cada documento, por correo o por
    carga. Ante un fallo transitorio reintenta con la misma espera que
    ``procesar_correo``; agotados los reintentos, el documento queda en
    ``error`` y se puede repetir con ``verificar/`` o con
    ``verificar_documentos``.
    """
    from apps.recepcion import verificacion
    from apps.recepcion.models import Documento

    documento = Documento.objects.select_related("emisor").filter(pk=documento_id).first()
    if documento is None:
        return
    try:
        verificacion.verificar(documento)
    except verificacion.ErrorTransitorio as error:
        if self.request.retries >= self.max_retries:
            verificacion.marcar_error(documento, str(error))
            return
        raise self.retry(exc=error, countdown=60 * 2 ** self.request.retries)
