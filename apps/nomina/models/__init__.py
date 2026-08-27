"""
Modelos de la nómina electrónica (Res. 000013/2021).

App aparte de `documentos` porque el XML de nómina no es UBL: raíz propia,
namespace propio, la información en atributos y sin resolución, impuestos ni
adquiriente. Lo que sí se comparte —emisor, software, certificado y el catálogo
de estados— se referencia, no se duplica. Ver `docs/anexo-nomina.md`.
"""
from .consecutivo_archivo import ConsecutivoArchivo
from .empleado import Empleado
from .nomina import Nomina
from .nomina_concepto import NominaConcepto
from .nomina_error import NominaError

__all__ = [
    "Empleado",
    "Nomina",
    "NominaConcepto",
    "NominaError",
    "ConsecutivoArchivo",
]
