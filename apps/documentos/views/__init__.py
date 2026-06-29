"""API de documentos electrónicos."""
from .adquiriente import AdquirienteViewSet
from .documento import DocumentoElectronicoViewSet

__all__ = ["AdquirienteViewSet", "DocumentoElectronicoViewSet"]
