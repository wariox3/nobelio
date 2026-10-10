"""Vistas de la recepción de documentos de proveedores."""
from .adjunto import AdjuntoViewSet
from .correo import CorreoViewSet
from .documento import DocumentoRecibidoViewSet
from .evento import EventoViewSet
from .inbound import inbound

__all__ = [
    "AdjuntoViewSet", "CorreoViewSet", "DocumentoRecibidoViewSet", "EventoViewSet", "inbound",
]
