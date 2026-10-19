"""Descartar un documento recibido: el emisor no hará nada más con él.

El documento queda intacto (datos, archivos y eventos), con
``estado_descartado`` y ``estado_recepcion`` en ``True``. Desde ahí no admite
eventos RADIAN, tampoco el acuse automático (``eventos._validar_documento``).
"""
import logging

from django.db import transaction

from apps.nucleo.registro import campos
from apps.emisores.models import Emisor
from apps.recepcion.models import Documento

logger = logging.getLogger(__name__)


class DocumentoNoDescartable(Exception):
    """El documento no se puede descartar. El mensaje dice por qué."""


def descartar(documento, *, usuario=None):
    """Marca ``documento`` como descartado y termina su recepción.

    Lanza ``DocumentoNoDescartable`` si ya está descartado, si su recepción ya
    terminó o si tiene algún evento RADIAN, en cualquier estado.
    """
    with transaction.atomic():
        # El mismo candado que toma ``eventos.solicitar``: un evento pedido a
        # la vez no se cuela entre la revisión y el guardado.
        Emisor.objects.select_for_update().get(pk=documento.emisor_id)
        documento = Documento.objects.get(pk=documento.pk)
        if documento.estado_descartado:
            raise DocumentoNoDescartable("El documento ya está descartado.")
        if documento.estado_recepcion:
            raise DocumentoNoDescartable(
                "La recepción del documento ya terminó: no hay nada que descartar."
            )
        evento = documento.eventos.select_related("evento_radian").order_by("creado_en").first()
        if evento is not None:
            raise DocumentoNoDescartable(
                f"El documento tiene eventos RADIAN (el {evento.evento_radian.codigo}, "
                f"{evento.numero}, {evento.get_estado_display().lower()}): "
                "solo se descarta uno sin eventos."
            )
        documento.estado_descartado = True
        documento.estado_recepcion = True
        documento.save(update_fields=["estado_descartado", "estado_recepcion", "actualizado_en"])
    logger.info("recepcion.documento_descartado %s", campos(
        documento=documento.pk, usuario=getattr(usuario, "pk", None),
    ))
    return documento
