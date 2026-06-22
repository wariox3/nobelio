"""API de documentos electrónicos."""
from .adquirente import AdquirenteViewSet
from .documento import DocumentoElectronicoViewSet

__all__ = ["AdquirenteViewSet", "DocumentoElectronicoViewSet"]
