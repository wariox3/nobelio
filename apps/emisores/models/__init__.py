"""
Modelos de emisores (Obligados a Facturar Electrónicamente — OFE).

Incluye los datos del facturador, su software registrado en la DIAN, el
certificado digital de firma y las resoluciones de numeración (rangos y clave
técnica).
"""
from .certificado import Certificado
from .emisor import Emisor
from .resolucion import (
    ResolucionFacturacion,
    mensaje_resolucion_ocupada,
    resolucion_activa_en_otra_cuenta,
)
from .software import SoftwareDian

__all__ = [
    "Emisor",
    "SoftwareDian",
    "Certificado",
    "ResolucionFacturacion",
    "resolucion_activa_en_otra_cuenta",
    "mensaje_resolucion_ocupada",
]
