"""Servicios de dominio de la app documentos."""
from .notificacion import (
    TAMANO_MAXIMO_ADJUNTOS,
    ErrorNotificacion,
    Paquete,
    empaquetar_notificacion,
    marcar_notificado,
)

__all__ = [
    "TAMANO_MAXIMO_ADJUNTOS",
    "ErrorNotificacion",
    "Paquete",
    "empaquetar_notificacion",
    "marcar_notificado",
]
