"""
Generación del XML UBL 2.1 para la DIAN.

Construye los documentos electrónicos (factura de venta, nota crédito, nota
débito y documento soporte) conforme al Anexo Técnico v1.9, incluyendo las
extensiones DIAN (sts:DianExtensions). El XML resultante NO está firmado: el
módulo ``apps/dian/firma`` añade la firma XAdES-EPES en una segunda
``UBLExtension``.

Una clase base ``ConstructorUBL`` concentra la lógica común; las subclases
parametrizan las diferencias de cada tipo de documento (raíz, código de tipo,
nombre de la línea/cantidad, total monetario, CUFE vs CUDE y referencias).

Referencia: ``Ejemplificaciones/.../{Generica,DebitNote}.xml`` y
``docs/anexo-tecnico.md``.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from lxml import etree

from apps.dian import identificadores as ident

# --- Namespaces UBL / DIAN --------------------------------------------------
NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
    "xades141": "http://uri.etsi.org/01903/v1.4.1#",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# Namespace del elemento raíz según el tipo de documento.
NS_RAIZ = {
    "Invoice": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "CreditNote": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "DebitNote": "urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2",
}

# NIT de la DIAN (proveedor de autorización).
NIT_DIAN = "800197268"

# Literal del agente del esquema (schemeAgencyName) que exige la DIAN en CompanyID.
AGENCIA_DIAN = "CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)"

# Códigos de tributo relevantes para el CUFE/CUDE.
COD_IVA = "01"
COD_ICA = "03"
COD_INC = "04"


def _q(prefijo: str, etiqueta: str) -> etree.QName:
    """Construye un QName a partir de un prefijo de namespace y la etiqueta."""
    return etree.QName(NS[prefijo], etiqueta)


def _sub(padre, prefijo, etiqueta, texto=None, **atributos):
    """Crea un subelemento con namespace, texto y atributos opcionales."""
    elem = etree.SubElement(padre, _q(prefijo, etiqueta))
    if texto is not None:
        elem.text = str(texto)
    for clave, valor in atributos.items():
        if valor is not None:
            elem.set(clave, str(valor))
    return elem


def _nsmap(raiz_ns: str) -> dict:
    """Mapa de namespaces para el elemento raíz, con el default correcto."""
    return {**NS, None: raiz_ns}


def _valor(monto) -> str:
    """Formatea un monto con 2 decimales (formato UBL)."""
    return f"{Decimal(monto).quantize(Decimal('0.01')):.2f}"


def _cantidad(valor) -> str:
    """Formatea una cantidad con 6 decimales (formato UBL)."""
    return f"{Decimal(valor).quantize(Decimal('0.000001')):.6f}"


def agrupar_impuestos(documento) -> "OrderedDict[str, dict]":
    """Agrupa los impuestos del documento por código de tributo."""
    grupos: "OrderedDict[str, dict]" = OrderedDict()
    for linea in documento.detalles.all():
        for imp in linea.impuestos.all():
            codigo = imp.tributo.codigo
            grupo = grupos.setdefault(
                codigo,
                {"nombre": imp.tributo.nombre, "base": Decimal("0"),
                 "valor": Decimal("0"), "tarifa": imp.tarifa},
            )
            grupo["base"] += imp.base_gravable
            grupo["valor"] += imp.valor
    return grupos


def _valor_por_tributo(grupos, codigo) -> Decimal:
    return grupos[codigo]["valor"] if codigo in grupos else Decimal("0")


class ConstructorUBL:
    """Base para construir el XML UBL 2.1 de un documento electrónico DIAN."""

    # Parámetros que diferencian cada tipo de documento (sobreescritos abajo).
    nombre_raiz = "Invoice"
    etiqueta_tipo = "InvoiceTypeCode"
    codigo_tipo = "01"
    etiqueta_linea = "InvoiceLine"
    etiqueta_cantidad = "InvoicedQuantity"
    etiqueta_total = "LegalMonetaryTotal"
    scheme_name = ident.SCHEME_NAME_CUFE
    usa_cude = False
    incluir_control = True  # sts:InvoiceControl (resolución) solo en factura
    customization_id_default = "10"

    def __init__(
        self,
        documento,
        *,
        software,
        ambiente: int,
        resolucion=None,
        clave_tecnica: str = "",
        pin: str = "",
        customization_id: str | None = None,
        concepto: str = "1",
    ):
        self.doc = documento
        self.software = software
        self.ambiente = ambiente
        self.resolucion = resolucion
        self.clave_tecnica = clave_tecnica or (resolucion.clave_tecnica if resolucion else "")
        self.pin = pin or (software.pin if software else "")
        self.customization_id = customization_id or self.customization_id_default
        self.concepto = concepto
        self.moneda = documento.moneda.codigo
        self.impuestos = agrupar_impuestos(documento)

    # -- API pública --------------------------------------------------------

    def calcular_identificador(self) -> str:
        comun = dict(
            numero_factura=self.doc.numero,
            fecha=self.doc.fecha_emision,
            hora=self.doc.hora_emision,
            valor_sin_impuestos=self.doc.valor_bruto,
            valor_iva=_valor_por_tributo(self.impuestos, COD_IVA),
            valor_inc=_valor_por_tributo(self.impuestos, COD_INC),
            valor_ica=_valor_por_tributo(self.impuestos, COD_ICA),
            valor_total=self.doc.total_a_pagar,
            nit_emisor=self.doc.emisor.numero_identificacion,
            id_adquirente=self.doc.adquiriente.numero_identificacion,
            tipo_ambiente=self.ambiente,
        )
        if self.usa_cude:
            return ident.calcular_cude(**comun, pin_software=self.pin)
        return ident.calcular_cufe(**comun, clave_tecnica=self.clave_tecnica)

    def construir(self) -> etree._Element:
        cufe = self.doc.cufe_cude or self.calcular_identificador()
        self.cufe = cufe
        raiz_ns = NS_RAIZ[self.nombre_raiz]

        raiz = etree.Element(etree.QName(raiz_ns, self.nombre_raiz), nsmap=_nsmap(raiz_ns))
        raiz.set(
            _q("xsi", "schemaLocation"),
            f"{raiz_ns} http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/"
            f"UBL-{self.nombre_raiz}-2.1.xsd",
        )

        self._extensiones(raiz, cufe)
        self._cabecera(raiz, cufe)
        self._referencias(raiz)
        self._parte_emisor(raiz)
        self._parte_adquirente(raiz)
        self._medios_pago(raiz)
        self._totales_impuestos(raiz)
        self._total_monetario(raiz)
        self._lineas(raiz)
        return raiz

    def generar_xml(self) -> bytes:
        return etree.tostring(
            self.construir(), xml_declaration=True, encoding="UTF-8", standalone=False
        )

    # -- Secciones ----------------------------------------------------------

    def _extensiones(self, raiz, cufe):
        extensiones = _sub(raiz, "ext", "UBLExtensions")
        ext1 = _sub(extensiones, "ext", "UBLExtension")
        contenido = _sub(ext1, "ext", "ExtensionContent")
        dian = _sub(contenido, "sts", "DianExtensions")

        if self.incluir_control and self.resolucion is not None:
            control = _sub(dian, "sts", "InvoiceControl")
            _sub(control, "sts", "InvoiceAuthorization", self.resolucion.numero_resolucion)
            periodo = _sub(control, "sts", "AuthorizationPeriod")
            _sub(periodo, "cbc", "StartDate", self.resolucion.vigente_desde.isoformat())
            _sub(periodo, "cbc", "EndDate", self.resolucion.vigente_hasta.isoformat())
            autorizadas = _sub(control, "sts", "AuthorizedInvoices")
            _sub(autorizadas, "sts", "Prefix", self.resolucion.prefijo)
            _sub(autorizadas, "sts", "From", self.resolucion.rango_desde)
            _sub(autorizadas, "sts", "To", self.resolucion.rango_hasta)

        fuente = _sub(dian, "sts", "InvoiceSource")
        _sub(fuente, "cbc", "IdentificationCode", "CO",
             listAgencyID="6",
             listAgencyName="United Nations Economic Commission for Europe",
             listSchemeURI="urn:oasis:names:specification:ubl:codelist:gc:CountryIdentificationCode-2.1")

        proveedor = _sub(dian, "sts", "SoftwareProvider")
        _sub(proveedor, "sts", "ProviderID", self.software.id_proveedor,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID="4", schemeName="31")
        _sub(proveedor, "sts", "SoftwareID", self.software.identificador,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN)

        codigo_seguridad = ident.calcular_codigo_seguridad_software(
            id_software=self.software.identificador, pin=self.software.pin,
            numero_documento=self.doc.numero,
        )
        _sub(dian, "sts", "SoftwareSecurityCode", codigo_seguridad,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN)

        autorizador = _sub(dian, "sts", "AuthorizationProvider")
        _sub(autorizador, "sts", "AuthorizationProviderID", NIT_DIAN,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID="4", schemeName="31")

        _sub(dian, "sts", "QRCode", self._url_qr(cufe))
        # La 2ª UBLExtension (firma XAdES) la añade el módulo de firma.

    def _cabecera(self, raiz, cufe):
        _sub(raiz, "cbc", "UBLVersionID", "UBL 2.1")
        _sub(raiz, "cbc", "CustomizationID", self.customization_id)
        _sub(raiz, "cbc", "ProfileID", "DIAN 2.1: Factura Electrónica de Venta")
        _sub(raiz, "cbc", "ProfileExecutionID", self.ambiente)
        _sub(raiz, "cbc", "ID", self.doc.numero)
        _sub(raiz, "cbc", "UUID", cufe, schemeID=str(self.ambiente), schemeName=self.scheme_name)
        _sub(raiz, "cbc", "IssueDate", self.doc.fecha_emision.isoformat())
        _sub(raiz, "cbc", "IssueTime", ident.formatear_hora(self.doc.hora_emision))
        # El UBL DebitNote no tiene elemento de tipo (etiqueta_tipo = None).
        if self.etiqueta_tipo:
            _sub(raiz, "cbc", self.etiqueta_tipo, self.codigo_tipo)
        _sub(raiz, "cbc", "DocumentCurrencyCode", self.moneda,
             listAgencyID="6",
             listAgencyName="United Nations Economic Commission for Europe",
             listID="ISO 4217 Alpha")
        _sub(raiz, "cbc", "LineCountNumeric", self.doc.detalles.count())

    def _referencias(self, raiz):
        """Referencias a la factura corregida (solo notas)."""
        return  # la factura no lleva referencias

    def _parte_emisor(self, raiz):
        emisor = self.doc.emisor
        sup = _sub(raiz, "cac", "AccountingSupplierParty")
        _sub(sup, "cbc", "AdditionalAccountID", self._codigo_organizacion(emisor))
        party = _sub(sup, "cac", "Party")
        nombre = _sub(party, "cac", "PartyName")
        _sub(nombre, "cbc", "Name", emisor.nombre_comercial or emisor.razon_social)
        self._direccion_fisica(party, emisor)
        self._party_tax_scheme(party, emisor)
        codigo_sucursal = (
            self.resolucion.prefijo if self.resolucion and self.resolucion.prefijo else None
        )
        self._party_legal_entity(party, emisor, codigo_sucursal=codigo_sucursal)
        if emisor.correo or emisor.telefono:
            contacto = _sub(party, "cac", "Contact")
            if emisor.telefono:
                _sub(contacto, "cbc", "Telephone", emisor.telefono)
            if emisor.correo:
                _sub(contacto, "cbc", "ElectronicMail", emisor.correo)

    def _parte_adquirente(self, raiz):
        adq = self.doc.adquiriente
        cli = _sub(raiz, "cac", "AccountingCustomerParty")
        _sub(cli, "cbc", "AdditionalAccountID", self._codigo_organizacion(adq))
        party = _sub(cli, "cac", "Party")
        # FAK61/FAK62: identificación del adquiriente (obligatoria para persona natural).
        ident = _sub(party, "cac", "PartyIdentification")
        _sub(ident, "cbc", "ID", adq.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=adq.digito_verificacion or "0",
             schemeName=adq.tipo_identificacion.codigo)
        nombre = _sub(party, "cac", "PartyName")
        _sub(nombre, "cbc", "Name", adq.razon_social)
        if adq.municipio:
            self._direccion_fisica(party, adq)
        self._party_tax_scheme(party, adq)
        self._party_legal_entity(party, adq)
        if adq.correo or adq.telefono:
            contacto = _sub(party, "cac", "Contact")
            if adq.telefono:
                _sub(contacto, "cbc", "Telephone", adq.telefono)
            if adq.correo:
                _sub(contacto, "cbc", "ElectronicMail", adq.correo)

    def _medios_pago(self, raiz):
        medios = _sub(raiz, "cac", "PaymentMeans")
        forma = self.doc.forma_pago.codigo if self.doc.forma_pago else "1"
        _sub(medios, "cbc", "ID", forma)
        _sub(medios, "cbc", "PaymentMeansCode",
             self.doc.medio_pago.codigo if self.doc.medio_pago else "10")

    def _totales_impuestos(self, raiz):
        for codigo, datos in self.impuestos.items():
            tax_total = _sub(raiz, "cac", "TaxTotal")
            _sub(tax_total, "cbc", "TaxAmount", _valor(datos["valor"]), currencyID=self.moneda)
            subtotal = _sub(tax_total, "cac", "TaxSubtotal")
            _sub(subtotal, "cbc", "TaxableAmount", _valor(datos["base"]), currencyID=self.moneda)
            _sub(subtotal, "cbc", "TaxAmount", _valor(datos["valor"]), currencyID=self.moneda)
            categoria = _sub(subtotal, "cac", "TaxCategory")
            _sub(categoria, "cbc", "Percent", _valor(datos["tarifa"]))
            esquema = _sub(categoria, "cac", "TaxScheme")
            _sub(esquema, "cbc", "ID", codigo)
            _sub(esquema, "cbc", "Name", datos["nombre"])

    def _total_monetario(self, raiz):
        total = _sub(raiz, "cac", self.etiqueta_total)
        bruto = self.doc.valor_bruto
        impuestos = self.doc.total_impuestos
        _sub(total, "cbc", "LineExtensionAmount", _valor(bruto), currencyID=self.moneda)
        _sub(total, "cbc", "TaxExclusiveAmount", _valor(bruto), currencyID=self.moneda)
        _sub(total, "cbc", "TaxInclusiveAmount", _valor(bruto + impuestos), currencyID=self.moneda)
        if self.doc.total_descuentos:
            _sub(total, "cbc", "AllowanceTotalAmount",
                 _valor(self.doc.total_descuentos), currencyID=self.moneda)
        if self.doc.total_cargos:
            _sub(total, "cbc", "ChargeTotalAmount",
                 _valor(self.doc.total_cargos), currencyID=self.moneda)
        _sub(total, "cbc", "PayableAmount", _valor(self.doc.total_a_pagar), currencyID=self.moneda)

    def _lineas(self, raiz):
        for linea in self.doc.detalles.all():
            il = _sub(raiz, "cac", self.etiqueta_linea)
            _sub(il, "cbc", "ID", linea.numero_linea)
            _sub(il, "cbc", self.etiqueta_cantidad, _cantidad(linea.cantidad),
                 unitCode=linea.unidad_medida.codigo)
            _sub(il, "cbc", "LineExtensionAmount", _valor(linea.valor_total), currencyID=self.moneda)
            self._linea_extra(il, linea)

            for imp in linea.impuestos.all():
                tt = _sub(il, "cac", "TaxTotal")
                _sub(tt, "cbc", "TaxAmount", _valor(imp.valor), currencyID=self.moneda)
                st = _sub(tt, "cac", "TaxSubtotal")
                _sub(st, "cbc", "TaxableAmount", _valor(imp.base_gravable), currencyID=self.moneda)
                _sub(st, "cbc", "TaxAmount", _valor(imp.valor), currencyID=self.moneda)
                cat = _sub(st, "cac", "TaxCategory")
                _sub(cat, "cbc", "Percent", _valor(imp.tarifa))
                esq = _sub(cat, "cac", "TaxScheme")
                _sub(esq, "cbc", "ID", imp.tributo.codigo)
                _sub(esq, "cbc", "Name", imp.tributo.nombre)

            item = _sub(il, "cac", "Item")
            _sub(item, "cbc", "Description", linea.descripcion)
            if linea.codigo_producto:
                ident_item = _sub(item, "cac", "SellersItemIdentification")
                _sub(ident_item, "cbc", "ID", linea.codigo_producto)
            # FAZ09/FAZ10: identificación del bien/servicio según un estándar.
            # 999 = estándar de adopción del contribuyente (código interno).
            std_item = _sub(item, "cac", "StandardItemIdentification")
            _sub(std_item, "cbc", "ID", linea.codigo_producto or str(linea.numero_linea),
                 schemeID="999", schemeAgencyID="195")

            precio = _sub(il, "cac", "Price")
            _sub(precio, "cbc", "PriceAmount", _valor(linea.valor_unitario), currencyID=self.moneda)
            _sub(precio, "cbc", "BaseQuantity", _cantidad(linea.cantidad),
                 unitCode=linea.unidad_medida.codigo)

    def _linea_extra(self, il, linea):
        """Hook para campos de línea propios de la factura (FreeOfChargeIndicator)."""

    # -- Componentes de parte ----------------------------------------------

    def _direccion_fisica(self, party, entidad):
        ubicacion = _sub(party, "cac", "PhysicalLocation")
        direccion = _sub(ubicacion, "cac", "Address")
        self._cuerpo_direccion(direccion, entidad)

    def _cuerpo_direccion(self, direccion, entidad):
        if entidad.municipio:
            _sub(direccion, "cbc", "ID", entidad.municipio.codigo)
            _sub(direccion, "cbc", "CityName", entidad.municipio.nombre)
        if entidad.departamento:
            _sub(direccion, "cbc", "CountrySubentity", entidad.departamento.nombre)
            _sub(direccion, "cbc", "CountrySubentityCode", entidad.departamento.codigo)
        if getattr(entidad, "direccion", ""):
            linea = _sub(direccion, "cac", "AddressLine")
            _sub(linea, "cbc", "Line", entidad.direccion)
        pais = _sub(direccion, "cac", "Country")
        _sub(pais, "cbc", "IdentificationCode", entidad.pais.codigo)
        _sub(pais, "cbc", "Name", entidad.pais.nombre, languageID="es")

    def _party_tax_scheme(self, party, entidad):
        pts = _sub(party, "cac", "PartyTaxScheme")
        _sub(pts, "cbc", "RegistrationName", entidad.razon_social)
        _sub(pts, "cbc", "CompanyID", entidad.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=entidad.digito_verificacion or "0",
             schemeName=entidad.tipo_identificacion.codigo)
        _sub(pts, "cbc", "TaxLevelCode", self._responsabilidades(entidad), listName="05")
        direccion = _sub(pts, "cac", "RegistrationAddress")
        self._cuerpo_direccion(direccion, entidad)
        esquema = _sub(pts, "cac", "TaxScheme")
        _sub(esquema, "cbc", "ID", COD_IVA)
        _sub(esquema, "cbc", "Name", "IVA")

    def _party_legal_entity(self, party, entidad, codigo_sucursal=None):
        ple = _sub(party, "cac", "PartyLegalEntity")
        _sub(ple, "cbc", "RegistrationName", entidad.razon_social)
        _sub(ple, "cbc", "CompanyID", entidad.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=entidad.digito_verificacion or "0",
             schemeName=entidad.tipo_identificacion.codigo)
        # FAB10a: el prefijo (código de la sucursal/punto de facturación) debe
        # coincidir con el Prefix del InvoiceControl.
        if codigo_sucursal:
            crs = _sub(ple, "cac", "CorporateRegistrationScheme")
            _sub(crs, "cbc", "ID", codigo_sucursal)

    # -- Auxiliares ---------------------------------------------------------

    def _responsabilidades(self, entidad) -> str:
        codigos = [r.codigo for r in entidad.responsabilidades.all()]
        return ";".join(codigos) if codigos else "R-99-PN"

    def _codigo_organizacion(self, entidad) -> str:
        return entidad.tipo_organizacion.codigo if entidad.tipo_organizacion else "1"

    def _url_qr(self, cufe) -> str:
        subdominio = "catalogo-vpfe-hab" if self.ambiente == 2 else "catalogo-vpfe"
        return f"https://{subdominio}.dian.gov.co/document/searchqr?documentkey={cufe}"


class ConstructorFacturaUBL(ConstructorUBL):
    """Factura electrónica de venta (Invoice, InvoiceTypeCode=01, CUFE)."""

    def _linea_extra(self, il, linea):
        _sub(il, "cbc", "FreeOfChargeIndicator", "false")


class _ConstructorNotaUBL(ConstructorUBL):
    """Base de notas: usan CUDE, DiscrepancyResponse y BillingReference."""

    scheme_name = ident.SCHEME_NAME_CUDE
    usa_cude = True
    incluir_control = False

    def _referencias(self, raiz):
        ref = self.doc.documento_referencia
        if ref is None:
            return
        discrepancia = _sub(raiz, "cac", "DiscrepancyResponse")
        _sub(discrepancia, "cbc", "ReferenceID", ref.numero)
        _sub(discrepancia, "cbc", "ResponseCode", self.concepto)
        _sub(discrepancia, "cbc", "Description", self.doc.observaciones or "Corrección")

        billing = _sub(raiz, "cac", "BillingReference")
        idr = _sub(billing, "cac", "InvoiceDocumentReference")
        _sub(idr, "cbc", "ID", ref.numero)
        _sub(idr, "cbc", "UUID", ref.cufe_cude, schemeName=ident.SCHEME_NAME_CUFE)
        _sub(idr, "cbc", "IssueDate", ref.fecha_emision.isoformat())


class ConstructorNotaCredito(_ConstructorNotaUBL):
    """Nota crédito (CreditNote, CreditNoteTypeCode=91, CUDE)."""

    nombre_raiz = "CreditNote"
    etiqueta_tipo = "CreditNoteTypeCode"
    codigo_tipo = "91"
    etiqueta_linea = "CreditNoteLine"
    etiqueta_cantidad = "CreditedQuantity"
    etiqueta_total = "LegalMonetaryTotal"
    customization_id_default = "20"


class ConstructorNotaDebito(_ConstructorNotaUBL):
    """Nota débito (DebitNote, DebitNoteTypeCode=92, CUDE)."""

    nombre_raiz = "DebitNote"
    etiqueta_tipo = None  # el UBL DebitNote no define un elemento de tipo
    codigo_tipo = "92"
    etiqueta_linea = "DebitNoteLine"
    etiqueta_cantidad = "DebitedQuantity"
    etiqueta_total = "RequestedMonetaryTotal"
    customization_id_default = "30"


class ConstructorDocumentoSoporte(ConstructorUBL):
    """Documento soporte en adquisiciones a no obligados (Invoice tipo 05, CUDE).

    Nota: usa la estructura ``Invoice`` con InvoiceTypeCode=05 y CUDE. Las
    particularidades de roles del documento soporte se modelan vía emisor/
    adquirente; es una aproximación pendiente de validar contra la DIAN.
    """

    codigo_tipo = "05"
    scheme_name = ident.SCHEME_NAME_CUDE
    usa_cude = True
    customization_id_default = "10"

    def _linea_extra(self, il, linea):
        _sub(il, "cbc", "FreeOfChargeIndicator", "false")


# Mapeo tipo de documento -> constructor.
from apps.documentos.models import Documento as _Doc  # noqa: E402

CONSTRUCTORES = {
    _Doc.Tipo.FACTURA_VENTA: ConstructorFacturaUBL,
    _Doc.Tipo.NOTA_CREDITO: ConstructorNotaCredito,
    _Doc.Tipo.NOTA_DEBITO: ConstructorNotaDebito,
    _Doc.Tipo.DOCUMENTO_SOPORTE: ConstructorDocumentoSoporte,
}


def constructor_para(documento, **kwargs) -> ConstructorUBL:
    """Devuelve el constructor adecuado según el tipo del documento."""
    try:
        clase = CONSTRUCTORES[documento.tipo]
    except KeyError:
        raise ValueError(f"Tipo de documento no soportado para UBL: {documento.tipo}")
    return clase(documento, **kwargs)


def generar_xml_factura(documento, *, software, resolucion, ambiente, clave_tecnica,
                        customization_id="10") -> bytes:
    """Genera el XML UBL 2.1 (sin firmar) de una factura de venta."""
    return ConstructorFacturaUBL(
        documento, software=software, resolucion=resolucion, ambiente=ambiente,
        clave_tecnica=clave_tecnica, customization_id=customization_id,
    ).generar_xml()
