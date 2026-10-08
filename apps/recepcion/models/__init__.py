"""Modelos de la recepción de documentos de proveedores."""
from .adjunto import Adjunto
from .correo import Correo
from .documento import Documento, EstadoVerificacion

__all__ = ["Adjunto", "Correo", "Documento", "EstadoVerificacion"]
