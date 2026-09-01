"""
Modelos de documentos electrónicos: adquirientes, documento electrónico
(factura, notas, documento soporte), sus líneas e impuestos, y lo propio del
documento equivalente P.O.S. (los datos de la venta en caja).
"""
from .adquiriente import Adquiriente
from .consecutivo_archivo import ConsecutivoArchivoDocumentoEquivalente
from .documento import Documento
from .documento_detalle import DocumentoDetalle
from .documento_detalle_impuesto import DocumentoDetalleImpuesto
from .documento_error import DocumentoError
from .documento_estado import DocumentoEstado
from .documento_pos import DocumentoPOS
from .documento_tipo import DocumentoTipo

__all__ = [
    "Adquiriente",
    "Documento",
    "DocumentoTipo",
    "DocumentoEstado",
    "DocumentoDetalle",
    "DocumentoDetalleImpuesto",
    "DocumentoError",
    "DocumentoPOS",
    "ConsecutivoArchivoDocumentoEquivalente",
]
