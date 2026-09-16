"""API de documentos electrónicos."""
from .documento import DocumentoViewSet
from .documento_evento import DocumentoEventoViewSet

__all__ = ["DocumentoViewSet", "DocumentoEventoViewSet"]
