"""La aplicación de Celery de nobelio. El broker es RabbitMQ (`CELERY_BROKER_URL`).

Como `wsgi.py`, cae por defecto en los settings de desarrollo; en el servidor
los fija la unidad de systemd (`docs/despliegue.md`). En desarrollo:

    .venv/bin/celery -A config worker -l info \
        -Q emitir_documento,avisos_webhook,celery \
        --without-gossip --without-mingle --without-heartbeat

Las tareas viven en el ``tareas.py`` de cada app, no en ``tasks.py``: el código
del proyecto está en español, y así se descubren.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("nobelio")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(related_name="tareas")
