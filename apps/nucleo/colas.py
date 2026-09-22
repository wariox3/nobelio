"""Encolar tareas de Celery sin que el broker pueda tumbar a quien encola."""
import logging

from django.db import transaction

from apps.nucleo.registro import campos

logger = logging.getLogger(__name__)


def encolar(tarea, *args):
    """Encola ``tarea`` cuando se confirme la transacción en curso.

    Al confirmar y no antes: el worker puede tomarla en el acto, y si la
    transacción aún no se confirmó —o se deshace— buscaría un documento que no
    ve o que no existe. Fuera de una transacción, encola en el acto.

    Si el broker no responde, la petición sigue: lo que se estaba guardando ya
    se guardó. La tarea no queda encolada y el error va al log (y a Sentry);
    cada tarea tiene su camino manual para recuperar lo que no corrió.
    """
    def _encolar():
        try:
            tarea.delay(*args)
        except Exception:
            logger.exception("tarea.no_encolada %s", campos(tarea=tarea.name, args=args))

    transaction.on_commit(_encolar)
