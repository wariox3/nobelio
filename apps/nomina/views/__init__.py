"""API de la nómina electrónica."""
from .empleado import EmpleadoViewSet
from .nomina import NominaViewSet

__all__ = [
    "EmpleadoViewSet",
    "NominaViewSet",
]
