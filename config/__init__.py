# Carga la app de Celery con Django, para que `shared_task` la encuentre y las
# tareas se puedan encolar desde las vistas.
from .celery import app as celery_app

__all__ = ("celery_app",)
