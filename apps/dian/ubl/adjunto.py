"""AttachedDocument: el contenedor que se le entrega al comprador.

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



PERFILES_ADJUNTO = {
    _Tipo.Codigo.FACTURA_VENTA: (
        "Factura Electrónica de Venta", "Contenedor de Factura Electrónica"),
    _Tipo.Codigo.NOTA_CREDITO: (
        "Nota Crédito Electrónica", "Contenedor de Nota Crédito Electrónica"),
    _Tipo.Codigo.NOTA_DEBITO: (
        "Nota Débito Electrónica", "Contenedor de Nota Débito Electrónica"),
    _Tipo.Codigo.DOCUMENTO_SOPORTE: (
        "Documento Soporte Electrónico", "Contenedor de Documento Soporte"),
    _Tipo.Codigo.NOTA_AJUSTE: (
        "Nota de Ajuste Electrónica", "Contenedor de Nota de Ajuste"),
    _Tipo.Codigo.DOCUMENTO_EQUIVALENTE_POS: (
        "Documento Equivalente P.O.S.", "Contenedor de Documento Equivalente"),
    _Tipo.Codigo.NOTA_AJUSTE_DE_CREDITO: (
        "Nota de Ajuste Crédito al Documento Equivalente",
        "Contenedor de Nota de Ajuste al Documento Equivalente"),
    _Tipo.Codigo.NOTA_AJUSTE_DE_DEBITO: (
        "Nota de Ajuste Débito al Documento Equivalente",
        "Contenedor de Nota de Ajuste al Documento Equivalente"),
}

VALIDADOR_DIAN = "Unidad Especial Dirección de Impuestos y Aduanas Nacionales"
CODIGO_VALIDACION_ACEPTADO = "02"


class ConstructorAttachedDocument(ConstructorUBL):
    """Envuelve un documento ya validado junto al acuse de la DIAN.

    No es un documento fiscal más: no lleva CUFE propio ni DianExtensions, y su
    contenido son otros dos XML incrustados como texto —el documento firmado y
    el ApplicationResponse—. Hereda de ConstructorUBL solo para reutilizar los
    bloques de emisor y adquiriente, que son los mismos.
    """

    nombre_raiz = "AttachedDocument"
    customization_id_default = "Documentos adjuntos"

    def __init__(self, documento, *, xml_documento, application_response=b"", **kwargs):
        super().__init__(documento, **kwargs)
        self.xml_documento = xml_documento
        self.application_response = application_response
        perfil, contenedor = PERFILES_ADJUNTO.get(
            documento.documento_tipo.codigo, ("Documento Electrónico", "Contenedor")
        )
        self.profile_id = perfil
        self.nombre_contenedor = contenedor

    def construir(self) -> etree._Element:
        raiz_ns = NS_RAIZ_ADJUNTO
        raiz = etree.Element(etree.QName(raiz_ns, self.nombre_raiz), nsmap=_nsmap(raiz_ns))
        raiz.set(
            _q("xsi", "schemaLocation"),
            f"{raiz_ns} http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/"
            "UBL-AttachedDocument-2.1.xsd",
        )
        # Vacío: la firma que añade el firmador será la única extensión, como
        # en los AttachedDocument de los proveedores tecnológicos.
        _sub(raiz, "ext", "UBLExtensions")

        _sub(raiz, "cbc", "UBLVersionID", "UBL 2.1")
        _sub(raiz, "cbc", "CustomizationID", self.customization_id)
        _sub(raiz, "cbc", "ProfileID", self.profile_id)
        _sub(raiz, "cbc", "ProfileExecutionID", self.ambiente)
        _sub(raiz, "cbc", "ID", self.doc.numero)
        _sub(raiz, "cbc", "IssueDate", timezone.localdate().isoformat())
        _sub(raiz, "cbc", "IssueTime", ident.formatear_hora(timezone.localtime().time()))
        _sub(raiz, "cbc", "DocumentType", self.nombre_contenedor)
        _sub(raiz, "cbc", "ParentDocumentID", self.doc.numero)

        self._parte(raiz, "SenderParty", self.doc.emisor)
        self._parte(raiz, "ReceiverParty", self.doc.adquiriente)
        self._adjunto(raiz, "Attachment", self.xml_documento)
        self._referencia_al_acuse(raiz)
        return raiz

    def generar_xml(self) -> bytes:
        return etree.tostring(
            self.construir(), xml_declaration=True, encoding="UTF-8", standalone=False
        )

    # -- Bloques ------------------------------------------------------------

    def _parte(self, raiz, etiqueta, entidad):
        """SenderParty / ReceiverParty.

        Ojo: aquí no hay ``cac:Party`` dentro. A diferencia de
        AccountingSupplierParty, estos elementos ya *son* un PartyType, así que
        sus hijos cuelgan directamente.
        """
        party = _sub(raiz, "cac", etiqueta)
        nombre = _sub(party, "cac", "PartyName")
        _sub(nombre, "cbc", "Name",
             getattr(entidad, "nombre_comercial", "") or entidad.razon_social)
        if entidad.municipio:
            self._direccion_fisica(party, entidad)
        self._party_tax_scheme(party, entidad)
        self._party_legal_entity(party, entidad)
        if entidad.telefono or entidad.correo:
            contacto = _sub(party, "cac", "Contact")
            if entidad.telefono:
                _sub(contacto, "cbc", "Telephone", entidad.telefono)
            if entidad.correo:
                _sub(contacto, "cbc", "ElectronicMail", entidad.correo)

    def _adjunto(self, padre, etiqueta, contenido):
        """Un XML incrustado como texto dentro de ExternalReference/Description."""
        externa = _sub(_sub(padre, "cac", etiqueta), "cac", "ExternalReference")
        _sub(externa, "cbc", "MimeCode", "text/xml")
        _sub(externa, "cbc", "EncodingCode", "UTF-8")
        descripcion = _sub(externa, "cbc", "Description")
        # CDATA para que el XML incrustado viaje legible y no escapado.
        descripcion.text = etree.CDATA(contenido.decode("utf-8", errors="replace"))

    def _referencia_al_acuse(self, raiz):
        """El ApplicationResponse de la DIAN y el resultado de su validación."""
        linea = _sub(raiz, "cac", "ParentDocumentLineReference")
        _sub(linea, "cbc", "LineID", self.doc.cufe_cude)
        ref = _sub(linea, "cac", "DocumentReference")
        _sub(ref, "cbc", "ID", self.doc.numero)
        _sub(ref, "cbc", "UUID", self.doc.cufe_cude, schemeName=self.scheme_name)
        _sub(ref, "cbc", "IssueDate", self.doc.fecha_emision.isoformat())
        _sub(ref, "cbc", "DocumentType", "ApplicationResponse")
        if self.application_response:
            self._adjunto(ref, "Attachment", self.application_response)
        validacion = self.doc.fecha_validacion
        if validacion is not None:
            local = timezone.localtime(validacion)
            resultado = _sub(ref, "cac", "ResultOfVerification")
            _sub(resultado, "cbc", "ValidatorID", VALIDADOR_DIAN)
            _sub(resultado, "cbc", "ValidationResultCode", CODIGO_VALIDACION_ACEPTADO)
            _sub(resultado, "cbc", "ValidationDate", local.date().isoformat())
            _sub(resultado, "cbc", "ValidationTime", ident.formatear_hora(local.time()))
