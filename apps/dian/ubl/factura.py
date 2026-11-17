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

    @property
    def customization_id_default(self) -> str:
        """El tipo de operación que eligió el documento; sin él, estándar (10)."""
        tipo = self.doc.tipo_operacion
        return tipo.codigo if tipo is not None else CUSTOMIZATION_FACTURA_ESTANDAR

    def _linea_extra(self, il, linea):
        _sub(il, "cbc", "FreeOfChargeIndicator", "false")

    @property
    def es_transporte(self) -> bool:
        return self.customization_id == CUSTOMIZATION_FACTURA_TRANSPORTE

    def _atributos_id_linea(self, linea) -> dict:
        """En transporte toda línea dice si factura una remesa del RNDC o un
        servicio adicional (reglas FAV08c y FAV08d)."""
        if not self.es_transporte:
            return {}
        return {"schemeID": (
            REGISTRO_TRANSPORTE_REMESA if linea.remesa
            else REGISTRO_TRANSPORTE_SERVICIO_ADICIONAL
        )}

    def _propiedades_item(self, item, linea):
        """La remesa de la línea: radicado, consecutivo y flete con la cantidad
        transportada (reglas FBF02 a FBF05, numeral 19.2)."""
        if not (self.es_transporte and linea.remesa):
            return
        remesa = linea.remesa
        for nombre, valor in (
            (REMESA_RADICADO, remesa["radicado"]),
            (REMESA_CONSECUTIVO, remesa["consecutivo"]),
        ):
            propiedad = _sub(item, "cac", "AdditionalItemProperty")
            _sub(propiedad, "cbc", "Name", nombre)
            _sub(propiedad, "cbc", "Value", str(valor))
        propiedad = _sub(item, "cac", "AdditionalItemProperty")
        _sub(propiedad, "cbc", "Name", REMESA_VALOR_FLETE)
        _sub(propiedad, "cbc", "Value", str(remesa["valor_flete"]))
        _sub(propiedad, "cbc", "ValueQuantity", str(remesa["cantidad"]),
             unitCode=remesa["unidad"])


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
    # CustomizationID de la nota sin documento referenciado («sin referencia a
    # facturas»). `None` = esta nota no puede ir sin referencia.
    customization_id_sin_referencia = None

    def __init__(self, documento, *args, **kwargs):
        super().__init__(documento, *args, **kwargs)
        if (
            not documento.tiene_referencia
            and self.customization_id_sin_referencia
            and not kwargs.get("customization_id")
        ):
            self.customization_id = self.customization_id_sin_referencia

    def _discrepancia(self, raiz):
        # El concepto va siempre, también sin referencia: es el que dice por
        # qué se corrige. ReferenceID (el número corregido) solo si lo hay.
        # La referencia sale del documento de nobelio o del externo, igual.
        ref = self.doc.referencia_corregida
        if ref is None and not self.customization_id_sin_referencia:
            return
        if ref is None:
            self._periodo_sin_referencia(raiz)
        discrepancia = _sub(raiz, "cac", "DiscrepancyResponse")
        if ref is not None:
            _sub(discrepancia, "cbc", "ReferenceID", ref.numero)
        _sub(discrepancia, "cbc", "ResponseCode", self.concepto)
        _sub(discrepancia, "cbc", "Description", self.doc.observaciones or "Corrección")

    def _periodo_sin_referencia(self, raiz):
        """cac:InvoicePeriod del documento, que la nota sin referencia exige
        (CAE04): el periodo que modifica, dentro de un mes y de 30 días.

        Va con la fecha de la nota en el inicio y en el fin: una nota sin
        factura se declara en el periodo en que se emite. Va antes de
        DiscrepancyResponse, como manda el XSD.
        """
        fecha = self.doc.fecha_emision.isoformat()
        periodo = _sub(raiz, "cac", "InvoicePeriod")
        _sub(periodo, "cbc", "StartDate", fecha)
        _sub(periodo, "cbc", "EndDate", fecha)

    def _referencias(self, raiz):
        ref = self.doc.referencia_corregida
        if ref is None:
            return
        billing = _sub(raiz, "cac", "BillingReference")
        idr = _sub(billing, "cac", "InvoiceDocumentReference")
        _sub(idr, "cbc", "ID", ref.numero)
        _sub(idr, "cbc", "UUID", ref.cufe_cude, schemeName=self.scheme_name_referencia)
        _sub(idr, "cbc", "IssueDate", ref.fecha.isoformat())
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
    customization_id_sin_referencia = "22"


class ConstructorNotaDebito(_ConstructorNotaUBL):
    """Nota débito (DebitNote, DebitNoteTypeCode=92, CUDE)."""

    profile_id = "DIAN 2.1: Nota Débito de Factura Electrónica de Venta"
    nombre_raiz = "DebitNote"
    etiqueta_tipo = None  # el UBL DebitNote no define un elemento de tipo
    etiqueta_linea = "DebitNoteLine"
    etiqueta_cantidad = "DebitedQuantity"
    etiqueta_total = "RequestedMonetaryTotal"
    customization_id_default = "30"
    customization_id_sin_referencia = "32"
    permite_descuento_documento = False  # el DebitNoteType del XSD no lo define
