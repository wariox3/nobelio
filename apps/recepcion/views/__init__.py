"""Vistas de la recepción de documentos de proveedores."""
from .correo import CorreoViewSet
from .inbound import inbound

__all__ = ["CorreoViewSet", "inbound"]
