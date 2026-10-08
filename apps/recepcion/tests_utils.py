"""XML y correos de prueba para la recepción, con la forma de los de la DIAN."""
import io
import zipfile
from email.message import EmailMessage

CUFE = "a" * 96


def xml_documento(
    raiz="Invoice", *, numero="FE-100", cufe=CUFE, tipo="01", nit_proveedor="800123456",
    nit_receptor="901192048", fecha="2026-10-01", totales="LegalMonetaryTotal",
    ambiente=None,
):
    codigo = {"Invoice": "InvoiceTypeCode", "CreditNote": "CreditNoteTypeCode",
              "DebitNote": "DebitNoteTypeCode"}[raiz]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<{raiz} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{raiz}-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  {f"<cbc:ProfileExecutionID>{ambiente}</cbc:ProfileExecutionID>" if ambiente else ""}
  <cbc:ID>{numero}</cbc:ID>
  <cbc:UUID schemeName="CUFE-SHA384">{cufe}</cbc:UUID>
  <cbc:IssueDate>{fecha}</cbc:IssueDate>
  <cbc:IssueTime>10:20:30-05:00</cbc:IssueTime>
  <cbc:{codigo}>{tipo}</cbc:{codigo}>
  <cbc:DocumentCurrencyCode>COP</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyTaxScheme>
      <cbc:RegistrationName>Proveedor Ejemplo S.A.S.</cbc:RegistrationName>
      <cbc:CompanyID schemeID="7" schemeName="31">{nit_proveedor}</cbc:CompanyID>
    </cac:PartyTaxScheme>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyTaxScheme>
      <cbc:RegistrationName>Cliente</cbc:RegistrationName>
      <cbc:CompanyID schemeID="3" schemeName="31">{nit_receptor}</cbc:CompanyID>
    </cac:PartyTaxScheme>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="COP">19000.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:TaxTotal><cbc:TaxAmount currencyID="COP">800.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:{totales}>
    <cbc:LineExtensionAmount currencyID="COP">100000.00</cbc:LineExtensionAmount>
    <cbc:PayableAmount currencyID="COP">119800.00</cbc:PayableAmount>
  </cac:{totales}>
</{raiz}>""".encode()


def xml_attached(documento=None, *, validacion="02"):
    documento = documento or xml_documento()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>FE-100</cbc:ID>
  <cbc:DocumentType>Contenedor de Factura Electrónica</cbc:DocumentType>
  <cac:Attachment><cac:ExternalReference>
    <cbc:MimeCode>text/xml</cbc:MimeCode>
    <cbc:Description><![CDATA[{documento.decode()}]]></cbc:Description>
  </cac:ExternalReference></cac:Attachment>
  <cac:ParentDocumentLineReference><cac:DocumentReference>
    <cbc:DocumentType>ApplicationResponse</cbc:DocumentType>
    <cac:ResultOfVerification>
      <cbc:ValidationResultCode>{validacion}</cbc:ValidationResultCode>
      <cbc:ValidationDate>2026-10-01</cbc:ValidationDate>
      <cbc:ValidationTime>10:25:00-05:00</cbc:ValidationTime>
    </cac:ResultOfVerification>
  </cac:DocumentReference></cac:ParentDocumentLineReference>
</AttachedDocument>""".encode()


PDF = b"%PDF-1.4 representacion grafica"


def zip_con(**archivos):
    """Un ZIP con ``nombre=contenido`` (los puntos del nombre van como ``__``)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, contenido in archivos.items():
            zf.writestr(nombre.replace("__", "."), contenido)
    return buffer.getvalue()


def correo_con(*adjuntos, asunto="Factura FE-100"):
    """Un correo con ``(nombre, contenido, tipo)`` adjuntos, en bytes."""
    mensaje = EmailMessage()
    mensaje["From"] = "Proveedor <facturas@proveedor.example>"
    mensaje["To"] = "901192048@recepcion.rededoc.co"
    mensaje["Subject"] = asunto
    mensaje["Message-ID"] = "<abc123@proveedor.example>"
    mensaje.set_content("Adjunto la factura.")
    for nombre, contenido, tipo in adjuntos:
        if isinstance(contenido, EmailMessage):
            mensaje.add_attachment(contenido, filename=nombre)
            continue
        principal, secundario = tipo.split("/")
        mensaje.add_attachment(
            contenido, maintype=principal, subtype=secundario, filename=nombre,
        )
    return bytes(mensaje)


def crear_documento_recibido(
    correo, emisor, cufe, *, tipo="factura_venta", fecha="2026-10-01",
    proveedor="800999999", razon_social="Proveedor", moneda=None,
    xml=b"<AttachedDocument/>", xml_documento=None, pdf=None,
):
    """Un ``Documento`` con sus archivos como ``Adjunto`` del correo.

    ``cufe`` es un carácter: se repite hasta los 96 del CUFE.
    """
    from datetime import date

    from apps.documentos.models import DocumentoTipo
    from apps.recepcion import adjuntos
    from apps.recepcion.models import Adjunto, Documento

    documento = Documento.objects.create(
        numero=f"FE-{cufe}", cufe_cude=cufe * 96, fecha_emision=date.fromisoformat(fecha),
        proveedor_numero_identificacion=proveedor, proveedor_razon_social=razon_social,
        receptor_numero_identificacion=emisor.numero_identificacion,
        total_a_pagar="1000.00", emisor=emisor, correo=correo, moneda=moneda,
        documento_tipo=DocumentoTipo.objects.get(codigo=tipo),
    )
    subidos = []
    for contenido, nombre, rol in (
        (xml, f"ad-FE-{cufe}.xml", Adjunto.Rol.XML),
        (xml_documento, f"FE-{cufe}.xml", Adjunto.Rol.XML_DOCUMENTO),
        (pdf, f"FE-{cufe}.pdf", Adjunto.Rol.PDF),
    ):
        if contenido is not None:
            adjuntos.crear(
                correo, contenido, nombre=nombre, rol=rol, documento=documento,
                subidos=subidos,
            )
    return documento
