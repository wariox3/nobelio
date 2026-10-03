"""Modelos de la recepción de documentos de proveedores."""
from .adjunto import Adjunto
from .correo import Correo
from .documento import Documento

__all__ = ["Adjunto", "Correo", "Documento"]
