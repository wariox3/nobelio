"""Descartar un documento recibido: el emisor no hará nada más con él.

El documento queda intacto (datos, archivos y eventos), con
``estado_descartado`` y ``estado_recepcion`` en ``True``. Desde ahí no admite
eventos RADIAN, tampoco el acuse automático (``eventos._validar_documento``).

``restaurar`` lo deshace: el documento vuelve a admitir eventos.
"""
import logging

from django.db import transaction

from apps.nucleo.registro import campos
from apps.emisores.models import Emisor
from apps.recepcion.models import Documento, EstadoVerificacion

logger = logging.getLogger(__name__)


class DocumentoNoDescartable(Exception):
    """El documento no se puede descartar. El mensaje dice por qué."""


class DocumentoNoRestaurable(Exception):
    """El documento no se puede restaurar. El mensaje dice por qué."""


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


def restaurar(documento, *, usuario=None):
    """Quita el descarte de ``documento``: vuelve a admitir eventos RADIAN.

    ``estado_recepcion`` vuelve a lo que diría la recepción sin el descarte:
    un descartado nunca tiene eventos, así que solo queda terminada una
    factura de contado verificada como válida. El acuse automático que se
    omitió mientras estuvo descartado no se pide solo: se pide a mano.

    Lanza ``DocumentoNoRestaurable`` si no está descartado.
    """
    from apps.recepcion import eventos, verificacion

    with transaction.atomic():
        # El mismo candado que el descarte y ``eventos.solicitar``.
        Emisor.objects.select_for_update().get(pk=documento.emisor_id)
        documento = Documento.objects.select_related("documento_tipo").get(pk=documento.pk)
        if not documento.estado_descartado:
            raise DocumentoNoRestaurable("El documento no está descartado.")
        documento.estado_descartado = False
        documento.estado_recepcion = (
            documento.verificacion_estado == EstadoVerificacion.VALIDO
            and verificacion._es_factura_de_contado(documento, eventos)
        )
        documento.save(update_fields=["estado_descartado", "estado_recepcion", "actualizado_en"])
    logger.info("recepcion.documento_restaurado %s", campos(
        documento=documento.pk, usuario=getattr(usuario, "pk", None),
    ))
    return documento
