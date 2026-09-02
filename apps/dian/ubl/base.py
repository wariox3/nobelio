"""
Generación del XML UBL 2.1 para la DIAN.

Construye los documentos electrónicos (factura de venta, nota crédito, nota
débito, documento soporte y su nota de ajuste) conforme al Anexo Técnico v1.9,
incluyendo las
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

from django.conf import settings
from django.utils import timezone
from lxml import etree

from apps.dian import identificadores as ident
from apps.utilidades.nit import digito_verificacion

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

NS_RAIZ_ADJUNTO = "urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"

# NIT de la DIAN (proveedor de autorización).
NIT_DIAN = "800197268"

# Literal del agente del esquema (schemeAgencyName) que exige la DIAN en CompanyID.
AGENCIA_DIAN = "CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)"

# Códigos de tributo relevantes para el CUFE/CUDE.
COD_IVA = "01"

# Literal exacto del cbc:ProfileID del documento soporte (regla DSAD03): 82
# caracteres que la DIAN compara tal cual, punto final incluido.
PROFILE_ID_DOCUMENTO_SOPORTE = (
    "DIAN 2.1: documento soporte en adquisiciones efectuadas a no obligados a facturar."
)

# Literal exacto del cbc:ProfileID de la nota de ajuste (regla NSAD03): 138
# caracteres, **sin** espacio final.
#
# El anexo y la ejemplificación oficial lo escriben con un espacio al final, y
# emitirlo así lo rechaza la DIAN (2026-08-27, documento NADS1) devolviendo un
# literal esperado que es byte a byte el que se le mandó. Su mensaje lo enmarca
# como “<literal> ”, con el mismo espacio de más que mete antes de los dos
# puntos en "ProfileID :": el espacio es del formato del mensaje, no del
# literal, y la comparación es por igualdad.
PROFILE_ID_NOTA_AJUSTE = (
    "DIAN 2.1: Nota de ajuste al documento soporte en adquisiciones efectuadas "
    "a sujetos no obligados a expedir factura o documento equivalente"
)

# Literal exacto del cbc:ProfileID del documento equivalente P.O.S.
# (regla DEAD03, Res. 000165/2023). Se compara tal cual, como los otros dos.
PROFILE_ID_DOCUMENTO_EQUIVALENTE_POS = "DIAN 2.1: Documento Equivalente POS"

# Literales exactos del cbc:ProfileID de las notas de ajuste al documento
# equivalente (reglas NAAD03 y NADBD03). Comprobados carácter a carácter contra
# las ejemplificaciones `NDAC.xml` y `NDADxml.xml`: **no llevan espacio final**,
# a diferencia de lo que aparentaba el del documento soporte y que costó un
# rechazo NSAD03.
PROFILE_ID_NOTA_AJUSTE_DE_CREDITO = (
    "DIAN 2.1: Nota de ajuste crédito al documento equivalente"
)
PROFILE_ID_NOTA_AJUSTE_DE_DEBITO = (
    "DIAN 2.1: Nota de ajuste débito al documento equivalente"
)

# cbc:CustomizationID de las notas del documento equivalente (numeral 16.4.2).
# Aquí no indica el tipo de operación como en la factura, sino **a qué
# documento equivalente se refiere la nota**: 20 es el tiquete P.O.S., 25 el de
# cine, 40 el de peajes… Como solo emitimos el P.O.S., es siempre 20.
CUSTOMIZATION_NOTA_AJUSTE_DE_POS = "20"

# cbc:DocumentTypeCode del documento referenciado en la nota de crédito: el
# tipo del documento equivalente que ajusta.
CODIGO_TIPO_REFERENCIA_POS = "20"

# @name del cbc:InvoiceTypeCode del P.O.S. **Ninguna regla lo valida**: no hay
# una sola mención a @name en las 318 reglas del numeral 10.2. Se emite porque
# la ejemplificación oficial lo trae y reproducirla es lo que sacó adelante la
# nómina tras el ZE02; no porque haga falta. Si alguna vez estorba, quitarlo es
# gratis.
NOMBRE_TIPO_DOCUMENTO_EQUIVALENTE_POS = "Factura tipo punto de venta POS"

# cbc:CustomizationID del documento soporte (lista TipoOperacion del anexo DS).
# Ojo: aquí no indica el tipo de operación como en la factura, sino de dónde es
# el vendedor, y de eso dependen qué campos de las partes son obligatorios.
CUSTOMIZATION_DS_RESIDENTE = "10"
CUSTOMIZATION_DS_NO_RESIDENTE = "11"

# Código de país de Colombia (ISO 3166-1 alfa-2), para decidir la procedencia.
COD_PAIS_COLOMBIA = "CO"

# cac:StandardItemIdentification: código y nombre del estándar con el que se
# identifica el bien o servicio. El 999 es el "de adopción del contribuyente"
# (código interno del emisor); el nombre es obligatorio (DSAZ13/FAZ10) y su
# literal es el de las ejemplificaciones oficiales.
COD_ESTANDAR_CONTRIBUYENTE = "999"
NOMBRE_ESTANDAR_CONTRIBUYENTE = "Estándar de adopción del contribuyente"

# schemeName del cbc:CompanyID cuando la parte se identifica con NIT. El
# documento soporte lo exige literal para el vendedor residente (DSAJ25a),
# aunque su identificación sea una cédula.
SCHEME_NAME_NIT = "31"

# cac:TaxScheme del vendedor en el documento soporte: el sujeto no obligado no
# es responsable de IVA, así que su tributo es "no aplica" (regla DSAJ40, que
# solo notifica). El del adquiriente sí es el IVA.
TRIBUTO_NO_APLICA = ("ZZ", "No aplica")
TRIBUTO_IVA = (COD_IVA, "IVA")

# TaxLevelCode cuando la entidad no declara ninguna responsabilidad. Es el "No
# responsable" que fijó el anexo técnico 1.8: sustituyó al "ZZ" ("No aplica")
# de la lista de 2019, que la DIAN rechaza hoy con FAJ26/FAK26.
SIN_RESPONSABILIDAD = "R-99-PN"
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
    # cbc:ProfileID: el literal exacto que la DIAN espera para cada tipo
    # (CAD03). Cada subclase pone el suyo.
    profile_id = "DIAN 2.1: Factura Electrónica de Venta"
    nombre_raiz = "Invoice"
    etiqueta_tipo = "InvoiceTypeCode"
    # @name del elemento de tipo. `None` = no se emite, que es lo que han hecho
    # siempre la factura, las notas y el documento soporte; solo el P.O.S. lo
    # pone. Va por atributo y no por sobreescritura de `_cabecera` para que
    # añadir un tipo no obligue a duplicar la cabecera entera.
    nombre_tipo = None
    etiqueta_linea = "InvoiceLine"
    etiqueta_cantidad = "InvoicedQuantity"
    etiqueta_total = "LegalMonetaryTotal"
    scheme_name = ident.SCHEME_NAME_CUFE
    usa_cude = False
    incluir_control = True  # sts:InvoiceControl (resolución) solo en factura
    incluir_vencimiento = True  # cbc:DueDate solo existe en el UBL Invoice
    # Dónde encaja cac:AllowanceCharge según el XSD de cada tipo: en el
    # documento, la nota débito no lo admite; en la línea, la factura lo pone
    # antes de los impuestos y las notas después.
    permite_descuento_documento = True
    descuento_linea_antes_de_impuestos = True
    customization_id_default = "10"
    # Si las retenciones van en cac:WithholdingTaxTotal, aparte de los demás
    # tributos. Solo el documento soporte: el anexo de factura no usa ese
    # elemento, así que allí las retenciones siguen saliendo como un TaxTotal
    # más, que es lo que se venía haciendo.
    emite_retenciones = False

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
        # El orden lo fija el XSD: DiscrepancyResponse, OrderReference y
        # BillingReference van en esa secuencia (ver UBL-CreditNote-2.1.xsd).
        self._discrepancia(raiz)
        self._orden_compra(raiz)
        self._referencias(raiz)
        self._parte_emisor(raiz)
        self._parte_adquirente(raiz)
        self._medios_pago(raiz)
        self._descuentos_documento(raiz)
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

        # Software propio: el proveedor tecnológico es el propio emisor, así que
        # el ProviderID es su NIT y el schemeID, su dígito de verificación.
        emisor = self.doc.emisor
        proveedor = _sub(dian, "sts", "SoftwareProvider")
        _sub(proveedor, "sts", "ProviderID", emisor.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=emisor.digito_verificacion or "0", schemeName="31")
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
        _sub(raiz, "cbc", "ProfileID", self.profile_id)
        _sub(raiz, "cbc", "ProfileExecutionID", self.ambiente)
        _sub(raiz, "cbc", "ID", self.doc.numero)
        _sub(raiz, "cbc", "UUID", cufe, schemeID=str(self.ambiente), schemeName=self.scheme_name)
        _sub(raiz, "cbc", "IssueDate", self.doc.fecha_emision.isoformat())
        _sub(raiz, "cbc", "IssueTime", ident.formatear_hora(self.doc.hora_emision))
        # DueDate solo existe en el UBL Invoice; las notas no lo tienen, y el
        # orden del esquema lo sitúa entre IssueTime y el código de tipo.
        if self.incluir_vencimiento and self.doc.fecha_vencimiento:
            _sub(raiz, "cbc", "DueDate", self.doc.fecha_vencimiento.isoformat())
        # El UBL DebitNote no tiene elemento de tipo (etiqueta_tipo = None).
        if self.etiqueta_tipo:
            _sub(raiz, "cbc", self.etiqueta_tipo, self.doc.documento_tipo.codigo_dian,
                 name=self.nombre_tipo)
        _sub(raiz, "cbc", "DocumentCurrencyCode", self.moneda,
             listAgencyID="6",
             listAgencyName="United Nations Economic Commission for Europe",
             listID="ISO 4217 Alpha")
        _sub(raiz, "cbc", "LineCountNumeric", self.doc.detalles.count())

    def _discrepancia(self, raiz):
        """Motivo de la corrección (solo notas)."""
        return  # la factura no corrige nada

    def _orden_compra(self, raiz):
        """cac:OrderReference: la orden de compra del adquiriente, si la hubo."""
        if not self.doc.orden_compra:
            return
        orden = _sub(raiz, "cac", "OrderReference")
        _sub(orden, "cbc", "ID", self.doc.orden_compra)
        if self.doc.orden_compra_fecha:
            _sub(orden, "cbc", "IssueDate", self.doc.orden_compra_fecha.isoformat())
        if self.doc.orden_compra_tipo:
            _sub(orden, "cbc", "OrderTypeCode", self.doc.orden_compra_tipo)
        if self.doc.orden_compra_documento:
            doc_ref = _sub(orden, "cac", "DocumentReference")
            _sub(doc_ref, "cbc", "ID", self.doc.orden_compra_documento)

    def _referencias(self, raiz):
        """Referencia al documento corregido (solo notas)."""
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
        # Person va después de Contact, y dentro de él el orden del XSD no es el
        # natural: FamilyName antes que MiddleName, y ResidenceAddress al final.
        self._persona(party, adq)

    def _persona(self, party, adq):
        """cac:Person: el nombre desglosado del adquiriente persona natural.

        Solo se emite si hay nombre desglosado; para una empresa no aplica y
        para una persona sin los campos cargados sería un bloque vacío.
        """
        campos = (
            ("FirstName", adq.primer_nombre),
            ("FamilyName", adq.primer_apellido),
            ("MiddleName", adq.segundo_nombre),
            ("OtherName", adq.segundo_apellido),
        )
        if not any(valor for _, valor in campos):
            return
        persona = _sub(party, "cac", "Person")
        _sub(persona, "cbc", "ID", adq.numero_identificacion)
        for etiqueta, valor in campos:
            if valor:
                _sub(persona, "cbc", etiqueta, valor)
        if adq.telefono or adq.correo:
            contacto = _sub(persona, "cac", "Contact")
            if adq.telefono:
                _sub(contacto, "cbc", "Telephone", adq.telefono)
            if adq.correo:
                _sub(contacto, "cbc", "ElectronicMail", adq.correo)
        if adq.municipio or adq.direccion:
            residencia = _sub(persona, "cac", "ResidenceAddress")
            self._cuerpo_direccion(residencia, adq)

    def _medios_pago(self, raiz):
        medios = _sub(raiz, "cac", "PaymentMeans")
        forma = self.doc.forma_pago.codigo if self.doc.forma_pago else "1"
        _sub(medios, "cbc", "ID", forma)
        _sub(medios, "cbc", "PaymentMeansCode",
             self.doc.medio_pago.codigo if self.doc.medio_pago else "10")
        if self.doc.fecha_vencimiento:
            _sub(medios, "cbc", "PaymentDueDate",
                 self.doc.fecha_vencimiento.isoformat())

    def _descuento_o_cargo(self, padre, *, secuencia, es_cargo, monto, base, motivo):
        """Un cac:AllowanceCharge: descuento (false) o cargo (true).

        El orden interno lo fija el XSD: ID, ChargeIndicator, Reason, Amount y
        BaseAmount. No se emite MultiplierFactorNumeric —el porcentaje— porque
        es opcional y deducirlo de monto/base introduciría un redondeo que la
        DIAN cuadraría contra los totales.
        """
        ac = _sub(padre, "cac", "AllowanceCharge")
        _sub(ac, "cbc", "ID", secuencia)
        _sub(ac, "cbc", "ChargeIndicator", "true" if es_cargo else "false")
        _sub(ac, "cbc", "AllowanceChargeReason",
             motivo or ("Cargo" if es_cargo else "Descuento"))
        _sub(ac, "cbc", "Amount", _valor(monto), currencyID=self.moneda)
        _sub(ac, "cbc", "BaseAmount", _valor(base), currencyID=self.moneda)

    def _descuentos_documento(self, raiz):
        """Descuentos y cargos globales, como los declara AllowanceTotalAmount."""
        if not self.permite_descuento_documento:
            return
        secuencia = 0
        if self.doc.total_descuentos:
            secuencia += 1
            self._descuento_o_cargo(
                raiz, secuencia=secuencia, es_cargo=False,
                monto=self.doc.total_descuentos, base=self.doc.valor_bruto,
                motivo=self.doc.descuentos_motivo,
            )
        if self.doc.total_cargos:
            secuencia += 1
            self._descuento_o_cargo(
                raiz, secuencia=secuencia, es_cargo=True,
                monto=self.doc.total_cargos, base=self.doc.valor_bruto,
                motivo=self.doc.cargos_motivo,
            )

    def _descuento_linea(self, il, linea):
        """El descuento de la línea. La base es el valor antes de descontarlo."""
        if not linea.descuento:
            return
        self._descuento_o_cargo(
            il, secuencia=1, es_cargo=False, monto=linea.descuento,
            base=linea.valor_total + linea.descuento,
            motivo=linea.descuento_motivo,
        )

    def _etiqueta_impuesto(self, codigo) -> str:
        """Dónde va este tributo: cac:TaxTotal o cac:WithholdingTaxTotal."""
        if self.emite_retenciones and codigo in _Tributo.CODIGOS_RETENCION:
            return "WithholdingTaxTotal"
        return "TaxTotal"

    def _bloque_impuesto(self, padre, etiqueta, *, codigo, nombre, tarifa, base, valor):
        """Un cac:TaxTotal (o WithholdingTaxTotal) con su único TaxSubtotal."""
        tax_total = _sub(padre, "cac", etiqueta)
        _sub(tax_total, "cbc", "TaxAmount", _valor(valor), currencyID=self.moneda)
        subtotal = _sub(tax_total, "cac", "TaxSubtotal")
        _sub(subtotal, "cbc", "TaxableAmount", _valor(base), currencyID=self.moneda)
        _sub(subtotal, "cbc", "TaxAmount", _valor(valor), currencyID=self.moneda)
        categoria = _sub(subtotal, "cac", "TaxCategory")
        _sub(categoria, "cbc", "Percent", _valor(tarifa))
        esquema = _sub(categoria, "cac", "TaxScheme")
        _sub(esquema, "cbc", "ID", codigo)
        _sub(esquema, "cbc", "Name", nombre)

    def _totales_impuestos(self, raiz):
        # El XSD manda el orden: primero todos los TaxTotal y después todos los
        # WithholdingTaxTotal, así que se recorre dos veces en vez de una.
        for etiqueta in ("TaxTotal", "WithholdingTaxTotal"):
            for codigo, datos in self.impuestos.items():
                if self._etiqueta_impuesto(codigo) != etiqueta:
                    continue
                self._bloque_impuesto(
                    raiz, etiqueta, codigo=codigo, nombre=datos["nombre"],
                    tarifa=datos["tarifa"], base=datos["base"], valor=datos["valor"],
                )

    def _total_monetario(self, raiz):
        total = _sub(raiz, "cac", self.etiqueta_total)
        bruto = self.doc.valor_bruto
        impuestos = self.doc.total_impuestos
        _sub(total, "cbc", "LineExtensionAmount", _valor(bruto), currencyID=self.moneda)
        _sub(total, "cbc", "TaxExclusiveAmount", _valor(bruto), currencyID=self.moneda)
        _sub(total, "cbc", "TaxInclusiveAmount", _valor(bruto + impuestos), currencyID=self.moneda)
        # Los cuatro se emiten siempre, aunque vayan en cero: es lo que hacen
        # los proveedores tecnológicos, y así el receptor lee todos los sumandos
        # del total sin tener que deducir cuáles se omitieron. El orden es el del
        # XSD (Allowance, Charge, Prepaid, PayableRounding, y al final Payable).
        _sub(total, "cbc", "AllowanceTotalAmount",
             _valor(self.doc.total_descuentos or 0), currencyID=self.moneda)
        _sub(total, "cbc", "ChargeTotalAmount",
             _valor(self.doc.total_cargos or 0), currencyID=self.moneda)
        # Anticipos y redondeo no se modelan todavía: van en cero, que es su
        # valor real mientras no existan los campos que los alimenten.
        _sub(total, "cbc", "PrepaidAmount", _valor(0), currencyID=self.moneda)
        _sub(total, "cbc", "PayableRoundingAmount", _valor(0), currencyID=self.moneda)
        _sub(total, "cbc", "PayableAmount", _valor(self.doc.total_a_pagar), currencyID=self.moneda)

    def _lineas(self, raiz):
        for linea in self.doc.detalles.all():
            il = _sub(raiz, "cac", self.etiqueta_linea)
            _sub(il, "cbc", "ID", linea.numero_linea)
            if linea.nota:
                _sub(il, "cbc", "Note", linea.nota)
            _sub(il, "cbc", self.etiqueta_cantidad, _cantidad(linea.cantidad),
                 unitCode=linea.unidad_medida.codigo)
            _sub(il, "cbc", "LineExtensionAmount", _valor(linea.valor_total), currencyID=self.moneda)
            if linea.centro_costo:
                _sub(il, "cbc", "AccountingCostCode", linea.centro_costo)
            self._linea_extra(il, linea)
            self._periodo_linea(il, linea)

            if self.descuento_linea_antes_de_impuestos:
                self._descuento_linea(il, linea)

            # Mismo orden que en el documento: los TaxTotal y luego, si el tipo
            # los separa, los WithholdingTaxTotal.
            impuestos_linea = list(linea.impuestos.all())
            for etiqueta in ("TaxTotal", "WithholdingTaxTotal"):
                for imp in impuestos_linea:
                    if self._etiqueta_impuesto(imp.tributo.codigo) != etiqueta:
                        continue
                    self._bloque_impuesto(
                        il, etiqueta, codigo=imp.tributo.codigo,
                        nombre=imp.tributo.nombre, tarifa=imp.tarifa,
                        base=imp.base_gravable, valor=imp.valor,
                    )

            if not self.descuento_linea_antes_de_impuestos:
                self._descuento_linea(il, linea)

            item = _sub(il, "cac", "Item")
            _sub(item, "cbc", "Description", linea.descripcion)
            if linea.marca:
                _sub(item, "cbc", "BrandName", linea.marca)
            if linea.modelo:
                _sub(item, "cbc", "ModelName", linea.modelo)
            if linea.codigo_producto:
                ident_item = _sub(item, "cac", "SellersItemIdentification")
                _sub(ident_item, "cbc", "ID", linea.codigo_producto)
            # FAZ09/FAZ10: identificación del bien/servicio según un estándar.
            # 999 = estándar de adopción del contribuyente (código interno).
            std_item = _sub(item, "cac", "StandardItemIdentification")
            _sub(std_item, "cbc", "ID", linea.codigo_producto or str(linea.numero_linea),
                 schemeID=COD_ESTANDAR_CONTRIBUYENTE, schemeAgencyID="195",
                 schemeName=NOMBRE_ESTANDAR_CONTRIBUYENTE)

            precio = _sub(il, "cac", "Price")
            _sub(precio, "cbc", "PriceAmount", _valor(linea.valor_unitario), currencyID=self.moneda)
            _sub(precio, "cbc", "BaseQuantity", _cantidad(linea.cantidad),
                 unitCode=linea.unidad_medida.codigo)

    def _periodo_linea(self, il, linea):
        """cac:InvoicePeriod de la línea: va tras FreeOfChargeIndicator y antes de TaxTotal."""
        if not (linea.periodo_desde or linea.periodo_hasta
                or linea.periodo_descripcion or linea.periodo_descripcion_codigo):
            return
        periodo = _sub(il, "cac", "InvoicePeriod")
        if linea.periodo_desde:
            _sub(periodo, "cbc", "StartDate", linea.periodo_desde.isoformat())
        if linea.periodo_hasta:
            _sub(periodo, "cbc", "EndDate", linea.periodo_hasta.isoformat())
        # Ojo al orden: el XSD pone DescriptionCode antes que Description.
        if linea.periodo_descripcion_codigo:
            _sub(periodo, "cbc", "DescriptionCode", linea.periodo_descripcion_codigo)
        if linea.periodo_descripcion:
            _sub(periodo, "cbc", "Description", linea.periodo_descripcion)

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
        # El XSD lo sitúa entre CityName y CountrySubentity.
        if getattr(entidad, "codigo_postal", ""):
            _sub(direccion, "cbc", "PostalZone", entidad.codigo_postal)
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
        """TaxLevelCode: los códigos de la lista TipoResponsabilidad, o ``R-99-PN``.

        ``R-99-PN`` ("No responsable") es el que puso el anexo técnico 1.8 para
        quien no tiene ninguna de las otras cuatro. El ``ZZ`` de la lista de
        2019 ya no vale: la DIAN lo rechaza con FAJ26/FAK26.
        """
        codigos = [r.codigo for r in entidad.responsabilidades.all()]
        return ";".join(codigos) if codigos else SIN_RESPONSABILIDAD

    def _codigo_organizacion(self, entidad) -> str:
        return entidad.tipo_organizacion.codigo if entidad.tipo_organizacion else "1"

    def _url_qr(self, cufe) -> str:
        subdominio = "catalogo-vpfe-hab" if self.ambiente == 2 else "catalogo-vpfe"
        return f"https://{subdominio}.dian.gov.co/document/searchqr?documentkey={cufe}"
