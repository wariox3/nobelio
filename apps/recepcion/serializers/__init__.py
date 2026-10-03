"""Serializers de la API de recepción."""
from .correo import CorreoSerializer
from .documento import DocumentoRecibidoResumenSerializer, DocumentoRecibidoSerializer

__all__ = [
    "CorreoSerializer",
    "DocumentoRecibidoResumenSerializer",
    "DocumentoRecibidoSerializer",
]
