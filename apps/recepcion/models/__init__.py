"""Modelos de la recepción de documentos de proveedores."""
from .adjunto import Adjunto
from .correo import Correo
from .documento import Documento, EstadoRadian, EstadoVerificacion
from .evento import ConsecutivoEvento, EstadoEvento, Evento

__all__ = [
    "Adjunto", "ConsecutivoEvento", "Correo", "Documento", "EstadoEvento", "EstadoRadian",
    "EstadoVerificacion", "Evento",
]
