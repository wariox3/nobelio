"""Serializers de la API de recepción."""
from .adjunto import AdjuntoSerializer
from .carga import CargaDocumentoSerializer, ResultadoCargaSerializer
from .correo import CorreoSerializer
from .documento import DocumentoRecibidoResumenSerializer, DocumentoRecibidoSerializer
from .evento import EventoSerializer, SolicitudEventoSerializer

__all__ = [
    "AdjuntoSerializer",
    "CargaDocumentoSerializer",
    "CorreoSerializer",
    "DocumentoRecibidoResumenSerializer",
    "DocumentoRecibidoSerializer",
    "EventoSerializer",
    "ResultadoCargaSerializer",
    "SolicitudEventoSerializer",
]
