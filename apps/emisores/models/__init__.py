"""
Modelos de emisores (Obligados a Facturar Electrónicamente — OFE).

Incluye los datos del facturador, su software registrado en la DIAN, el
certificado digital de firma y las resoluciones de numeración (rangos y clave
técnica).
"""
from .certificado import Certificado
from .emisor import Emisor
from .resolucion import ResolucionFacturacion
from .software import SoftwareDian

__all__ = [
    "Emisor",
    "SoftwareDian",
    "Certificado",
    "ResolucionFacturacion",
]
