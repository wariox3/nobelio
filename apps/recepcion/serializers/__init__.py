"""Serializers de la API de recepción."""
from .adjunto import AdjuntoSerializer
from .correo import CorreoSerializer
from .documento import DocumentoRecibidoResumenSerializer, DocumentoRecibidoSerializer

__all__ = [
    "AdjuntoSerializer",
    "CorreoSerializer",
    "DocumentoRecibidoResumenSerializer",
    "DocumentoRecibidoSerializer",
]
