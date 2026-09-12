"""Servicios de dominio de la app emisores (uno por modelo)."""
from .certificado_validacion import CertificadoInvalido, validar_pkcs12
from .emision import (
    MENSAJE_EMISOR_INACTIVO,
    MENSAJE_SIN_CERTIFICADO,
    certificado_del_emisor,
    motivo_no_puede_emitir,
)
from .factura_prueba import (
    FACTURAS_DE_PRUEBA,
    crear_factura_prueba,
    crear_documento_de_prueba,
    crear_facturas_de_prueba,
    sembrar_documentos_de_prueba,
    siguiente_consecutivo,
)
from .nomina_prueba import (
    NOMINAS_DE_PRUEBA,
    crear_nomina_de_prueba,
    crear_nomina_prueba,
    crear_nominas_de_prueba,
)
from .resolucion_pruebas import (
    RESOLUCION_SET_PRUEBAS,
    sembrar_resolucion_de_pruebas,
)

__all__ = [
    "CertificadoInvalido",
    "validar_pkcs12",
    "MENSAJE_EMISOR_INACTIVO",
    "MENSAJE_SIN_CERTIFICADO",
    "certificado_del_emisor",
    "motivo_no_puede_emitir",
    "FACTURAS_DE_PRUEBA",
    "crear_factura_prueba",
    "crear_documento_de_prueba",
    "crear_facturas_de_prueba",
    "siguiente_consecutivo",
    "sembrar_documentos_de_prueba",
    "NOMINAS_DE_PRUEBA",
    "crear_nomina_de_prueba",
    "crear_nomina_prueba",
    "crear_nominas_de_prueba",
    "RESOLUCION_SET_PRUEBAS",
    "sembrar_resolucion_de_pruebas",
]
