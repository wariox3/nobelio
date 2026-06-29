"""API de documentos electrónicos."""
from .adquiriente import AdquirienteViewSet
from .documento import DocumentoViewSet

__all__ = ["AdquirienteViewSet", "DocumentoViewSet"]
