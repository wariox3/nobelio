"""Factura de venta y sus notas crédito y débito.

Parte del paquete `apps.dian.ubl`. La superficie pública no cambió al partirlo:
`from apps.dian import ubl` sigue dando acceso a todo desde `__init__`.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from lxml import etree

from apps.dian import identificadores as ident
from apps.dian.ubl.base import *  # noqa: F401,F403  (constantes y perfiles)
from apps.dian.ubl.base import (  # los ayudantes privados no vienen en el `*`
    ConstructorUBL,
    _cantidad,
    _nsmap,
    _q,
    _sub,
    _valor,
    _valor_por_tributo,
    agrupar_impuestos,
)
from apps.documentos.models import DocumentoTipo as _Tipo



class ConstructorFacturaUBL(ConstructorUBL):
    """Factura electrónica de venta (Invoice, InvoiceTypeCode=01, CUFE)."""

    profile_id = "DIAN 2.1: Factura Electrónica de Venta"

    def _linea_extra(self, il, linea):
        _sub(il, "cbc", "FreeOfChargeIndicator", "false")


class _ConstructorNotaUBL(ConstructorUBL):
    """Base de notas: usan CUDE, DiscrepancyResponse y BillingReference."""

    scheme_name = ident.SCHEME_NAME_CUDE
    usa_cude = True
    incluir_control = False
    incluir_vencimiento = False
    descuento_linea_antes_de_impuestos = False
    # @schemeName del UUID del documento corregido, que es el suyo y no el de la
    # nota: una nota de factura referencia un CUFE y la de ajuste, un CUDS.
    scheme_name_referencia = ident.SCHEME_NAME_CUFE
    # cbc:DocumentTypeCode dentro de la referencia. `None` = no se emite, que es
    # lo que han hecho siempre las notas de factura y de documento soporte;
    # solo la nota de crédito del documento equivalente lo trae.
    codigo_tipo_referencia = None

    def _discrepancia(self, raiz):
        ref = self.doc.documento_referencia
        if ref is None:
            return
        discrepancia = _sub(raiz, "cac", "DiscrepancyResponse")
        _sub(discrepancia, "cbc", "ReferenceID", ref.numero)
        _sub(discrepancia, "cbc", "ResponseCode", self.concepto)
        _sub(discrepancia, "cbc", "Description", self.doc.observaciones or "Corrección")

    def _referencias(self, raiz):
        ref = self.doc.documento_referencia
        if ref is None:
            return
        billing = _sub(raiz, "cac", "BillingReference")
        idr = _sub(billing, "cac", "InvoiceDocumentReference")
        _sub(idr, "cbc", "ID", ref.numero)
        _sub(idr, "cbc", "UUID", ref.cufe_cude, schemeName=self.scheme_name_referencia)
        _sub(idr, "cbc", "IssueDate", ref.fecha_emision.isoformat())
        if self.codigo_tipo_referencia:
            _sub(idr, "cbc", "DocumentTypeCode", self.codigo_tipo_referencia)


class ConstructorNotaCredito(_ConstructorNotaUBL):
    """Nota crédito (CreditNote, CreditNoteTypeCode=91, CUDE)."""

    profile_id = "DIAN 2.1: Nota Crédito de Factura Electrónica de Venta"
    nombre_raiz = "CreditNote"
    etiqueta_tipo = "CreditNoteTypeCode"
    etiqueta_linea = "CreditNoteLine"
    etiqueta_cantidad = "CreditedQuantity"
    etiqueta_total = "LegalMonetaryTotal"
    customization_id_default = "20"


class ConstructorNotaDebito(_ConstructorNotaUBL):
    """Nota débito (DebitNote, DebitNoteTypeCode=92, CUDE)."""

    profile_id = "DIAN 2.1: Nota Débito de Factura Electrónica de Venta"
    nombre_raiz = "DebitNote"
    etiqueta_tipo = None  # el UBL DebitNote no define un elemento de tipo
    etiqueta_linea = "DebitNoteLine"
    etiqueta_cantidad = "DebitedQuantity"
    etiqueta_total = "RequestedMonetaryTotal"
    customization_id_default = "30"
    permite_descuento_documento = False  # el DebitNoteType del XSD no lo define
