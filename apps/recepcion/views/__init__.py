"""Vistas de la recepción de documentos de proveedores."""
from .correo import CorreoViewSet
from .documento import DocumentoRecibidoViewSet
from .inbound import inbound

__all__ = ["CorreoViewSet", "DocumentoRecibidoViewSet", "inbound"]
