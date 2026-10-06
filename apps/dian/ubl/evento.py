"""ApplicationResponse de un evento RADIAN del adquiriente (030 a 033).

Parte del paquete `apps.dian.ubl`. A diferencia de los demás constructores, no
parte de un documento de la base: recibe un ``Evento`` con lo que hace falta,
porque el evento es de una factura **recibida** (``rec_documento``), cuyo
emisor es un tercero que no está en la plataforma.

Como los demás, el XML sale **sin firmar**: la firma la añade
``apps.dian.firma``. Estructura de los ejemplos oficiales
(``apps/dian/datos/ejemplos/radian/``); resumen en ``docs/anexo-radian.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from lxml import etree

from apps.dian import identificadores as ident
from apps.dian.ubl.base import AGENCIA_DIAN, NIT_DIAN, _nsmap, _q, _sub
from apps.utilidades.nit import digito_verificacion

NS_RAIZ_EVENTO = "urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"

PROFILE_ID_EVENTO = "DIAN 2.1: ApplicationResponse de la Factura Electrónica de Venta"
# Tipo de operación de los eventos 030–034 en los ejemplos oficiales. Los de
# título valor usan los códigos del numeral 14.1.2 del anexo RADIAN.
CUSTOMIZATION_ID_EVENTO = "1"
# Tipo del documento referenciado: la factura electrónica de venta.
TIPO_FACTURA = "01"

ACUSE, RECLAMO, RECIBO, ACEPTACION = "030", "031", "032", "033"
EVENTOS = (ACUSE, RECLAMO, RECIBO, ACEPTACION)
# Los que identifican a la persona que recibió (cac:IssuerParty/cac:Person).
EVENTOS_CON_PERSONA = (ACUSE, RECIBO)

# schemeName del CompanyID cuando es un NIT, y schemeVersionID según el tipo
# de organización (1 jurídica, 2 natural).
SCHEME_NIT = "31"
PERSONA_JURIDICA, PERSONA_NATURAL = "1", "2"


def _dv(numero, tipo_identificacion):
    """El DV si la identificación es un NIT; si no, ``None`` y no se emite.

    Solo el NIT lleva DV: una cédula con uno inventado la rechaza la DIAN
    (``apps/utilidades/nit.py``). Los ejemplos oficiales lo ponen también a la
    persona con cédula, pero con valores de relleno que no cuadran.
    """
    if tipo_identificacion != SCHEME_NIT:
        return None
    return digito_verificacion(numero)


@dataclass(frozen=True)
class Parte:
    """Quien emite el evento (``SenderParty``) o quien lo recibe (``ReceiverParty``)."""

    razon_social: str
    numero_identificacion: str
    tipo_identificacion: str = SCHEME_NIT
    tipo_organizacion: str = PERSONA_JURIDICA


@dataclass(frozen=True)
class Persona:
    """La persona que recibió la factura o la mercancía (030 y 032)."""

    tipo_identificacion: str
    numero_identificacion: str
    nombres: str
    apellidos: str
    cargo: str = ""
    area: str = ""


@dataclass(frozen=True)
class Evento:
    """Lo que hace falta para el XML de un evento.

    ``emisor`` es quien genera el evento —el adquiriente de la factura, es
    decir, nuestro emisor— y ``receptor`` el facturador, el proveedor.
    """

    codigo: str
    descripcion: str
    numero: str
    fecha: date
    hora: time
    emisor: Parte
    receptor: Parte
    factura_numero: str
    factura_cufe: str
    factura_tipo: str = TIPO_FACTURA
    persona: Persona | None = None
    # (código, nombre) del concepto del reclamo, solo en el 031.
    concepto_reclamo: tuple[str, str] | None = None


class ConstructorEvento:
    """Construye el ``ApplicationResponse`` de un evento, sin firmar."""

    def __init__(self, evento: Evento, *, software, ambiente: int):
        self._validar(evento)
        self.evento = evento
        self.software = software
        self.ambiente = ambiente

    @staticmethod
    def _validar(evento):
        if evento.codigo not in EVENTOS:
            raise ValueError(f"Evento no soportado: {evento.codigo}.")
        if evento.codigo in EVENTOS_CON_PERSONA and evento.persona is None:
            raise ValueError(f"El evento {evento.codigo} lleva la persona que recibe.")
        if evento.codigo == RECLAMO and evento.concepto_reclamo is None:
            raise ValueError("El reclamo (031) lleva su concepto.")

    # -- API pública --------------------------------------------------------

    def calcular_cude(self) -> str:
        e = self.evento
        return ident.calcular_cude_evento(
            numero_evento=e.numero,
            fecha=e.fecha,
            hora=e.hora,
            nit_emisor_evento=e.emisor.numero_identificacion,
            id_receptor_evento=e.receptor.numero_identificacion,
            codigo_evento=e.codigo,
            numero_documento=e.factura_numero,
            tipo_documento=e.factura_tipo,
            pin_software=self.software.pin,
        )

    def construir(self) -> etree._Element:
        self.cude = self.calcular_cude()
        raiz = etree.Element(
            etree.QName(NS_RAIZ_EVENTO, "ApplicationResponse"), nsmap=_nsmap(NS_RAIZ_EVENTO),
        )
        raiz.set(
            _q("xsi", "schemaLocation"),
            f"{NS_RAIZ_EVENTO} http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/"
            "UBL-ApplicationResponse-2.1.xsd",
        )
        self._extensiones(raiz)
        self._cabecera(raiz)
        self._parte(raiz, "SenderParty", self.evento.emisor)
        self._parte(raiz, "ReceiverParty", self.evento.receptor)
        self._respuesta(raiz)
        return raiz

    def generar_xml(self) -> bytes:
        return etree.tostring(
            self.construir(), xml_declaration=True, encoding="UTF-8", standalone=False
        )

    # -- Secciones ----------------------------------------------------------

    def _extensiones(self, raiz):
        """``sts:DianExtensions`` como en la factura, sin ``InvoiceControl``."""
        dian = _sub(
            _sub(_sub(_sub(raiz, "ext", "UBLExtensions"), "ext", "UBLExtension"),
                 "ext", "ExtensionContent"),
            "sts", "DianExtensions",
        )
        fuente = _sub(dian, "sts", "InvoiceSource")
        _sub(fuente, "cbc", "IdentificationCode", "CO",
             listAgencyID="6",
             listAgencyName="United Nations Economic Commission for Europe",
             listSchemeURI="urn:oasis:names:specification:ubl:codelist:gc:CountryIdentificationCode-2.1")

        # Software propio: el proveedor tecnológico es quien emite el evento.
        # Como en la factura: con el DV del NIT, o "0" si no es NIT.
        emisor = self.evento.emisor
        proveedor = _sub(dian, "sts", "SoftwareProvider")
        _sub(proveedor, "sts", "ProviderID", emisor.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=_dv(emisor.numero_identificacion, emisor.tipo_identificacion) or "0",
             schemeName=SCHEME_NIT)
        _sub(proveedor, "sts", "SoftwareID", self.software.identificador,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN)

        codigo_seguridad = ident.calcular_codigo_seguridad_software(
            id_software=self.software.identificador, pin=self.software.pin,
            numero_documento=self.evento.numero,
        )
        _sub(dian, "sts", "SoftwareSecurityCode", codigo_seguridad,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN)

        autorizador = _sub(dian, "sts", "AuthorizationProvider")
        _sub(autorizador, "sts", "AuthorizationProviderID", NIT_DIAN,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=digito_verificacion(NIT_DIAN), schemeName=SCHEME_NIT)

        # Como en los ejemplos: el QR lleva al CUFE de la factura, no al CUDE.
        subdominio = "catalogo-vpfe-hab" if self.ambiente == 2 else "catalogo-vpfe"
        _sub(dian, "sts", "QRCode",
             f"https://{subdominio}.dian.gov.co/document/searchqr?documentkey="
             f"{self.evento.factura_cufe}")
        # La 2ª UBLExtension (firma XAdES) la añade el módulo de firma.

    def _cabecera(self, raiz):
        e = self.evento
        _sub(raiz, "cbc", "UBLVersionID", "UBL 2.1")
        _sub(raiz, "cbc", "CustomizationID", CUSTOMIZATION_ID_EVENTO)
        _sub(raiz, "cbc", "ProfileID", PROFILE_ID_EVENTO)
        _sub(raiz, "cbc", "ProfileExecutionID", self.ambiente)
        _sub(raiz, "cbc", "ID", e.numero)
        _sub(raiz, "cbc", "UUID", self.cude,
             schemeID=str(self.ambiente), schemeName=ident.SCHEME_NAME_CUDE)
        _sub(raiz, "cbc", "IssueDate", ident.formatear_fecha(e.fecha))
        _sub(raiz, "cbc", "IssueTime", ident.formatear_hora(e.hora))

    def _parte(self, raiz, etiqueta, parte):
        """SenderParty / ReceiverParty: solo ``cac:PartyTaxScheme``."""
        esquema = _sub(_sub(raiz, "cac", etiqueta), "cac", "PartyTaxScheme")
        _sub(esquema, "cbc", "RegistrationName", parte.razon_social)
        _sub(esquema, "cbc", "CompanyID", parte.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=_dv(parte.numero_identificacion, parte.tipo_identificacion),
             schemeName=parte.tipo_identificacion,
             schemeVersionID=parte.tipo_organizacion)
        tributo = _sub(esquema, "cac", "TaxScheme")
        _sub(tributo, "cbc", "ID", "01")
        _sub(tributo, "cbc", "Name", "IVA")

    def _respuesta(self, raiz):
        e = self.evento
        respuesta = _sub(raiz, "cac", "DocumentResponse")
        res = _sub(respuesta, "cac", "Response")
        if e.concepto_reclamo is not None:
            codigo, nombre = e.concepto_reclamo
            _sub(res, "cbc", "ResponseCode", e.codigo, name=nombre, listID=codigo)
        else:
            _sub(res, "cbc", "ResponseCode", e.codigo)
        _sub(res, "cbc", "Description", e.descripcion)

        referencia = _sub(respuesta, "cac", "DocumentReference")
        _sub(referencia, "cbc", "ID", e.factura_numero)
        _sub(referencia, "cbc", "UUID", e.factura_cufe, schemeName=ident.SCHEME_NAME_CUFE)
        _sub(referencia, "cbc", "DocumentTypeCode", e.factura_tipo)

        if e.persona is not None:
            p = e.persona
            persona = _sub(_sub(respuesta, "cac", "IssuerParty"), "cac", "Person")
            _sub(persona, "cbc", "ID", p.numero_identificacion,
                 schemeID=_dv(p.numero_identificacion, p.tipo_identificacion),
                 schemeName=p.tipo_identificacion)
            _sub(persona, "cbc", "FirstName", p.nombres)
            _sub(persona, "cbc", "FamilyName", p.apellidos)
            if p.cargo:
                _sub(persona, "cbc", "JobTitle", p.cargo)
            if p.area:
                _sub(persona, "cbc", "OrganizationDepartment", p.area)
