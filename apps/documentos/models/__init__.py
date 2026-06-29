"""
Modelos de documentos electrónicos: adquirientes, documento electrónico
(factura, notas, documento soporte), sus líneas e impuestos.
"""
from .adquiriente import Adquiriente
from .documento import Documento
from .documento_detalle import DocumentoDetalle
from .documento_detalle_impuesto import DocumentoDetalleImpuesto
from .documento_tipo import DocumentoTipo

__all__ = [
    "Adquiriente",
    "Documento",
    "DocumentoTipo",
    "DocumentoDetalle",
    "DocumentoDetalleImpuesto",
]
