"""El ApplicationResponse de los eventos RADIAN del adquiriente (030–033)."""
import base64
import datetime as dt
import hashlib
from dataclasses import replace
from datetime import date, time
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.test import SimpleTestCase
from lxml import etree

from apps.dian import firma, identificadores as ident, ubl
from apps.dian.tests_firma import _generar_certificado
from apps.dian.ubl.evento import ConstructorEvento, Evento, Parte, Persona

NS = ubl.NS
CUFE = "f2a09fc9a83fbc7b2a14e4adc89ef5917c96f375a3d102ce4e2d20d5cf64e1ca6ff8a618e3c14bb43a783a1595e8fc09"
SOFTWARE = SimpleNamespace(identificador="fa326ca7-c1f8-40d3-a6fc-24d7c1040607", pin="20191")
ESQUEMA = etree.XMLSchema(etree.parse(
    str(settings.DIAN_XSD_DIR / "maindoc" / "UBL-ApplicationResponse-2.1.xsd")
))

ACUSE = Evento(
    codigo="030",
    descripcion="Acuse de recibo de Factura Electrónica de Venta",
    numero="ACR0021",
    fecha=date(2020, 12, 12),
    hora=time(18, 30, 37),
    emisor=Parte("Pruebas", "900373979"),
    receptor=Parte("FACTURA ELECTRONICA USUARIO PRUEBAS MIGRACION", "900373972"),
    factura_numero="SETG980000358",
    factura_cufe=CUFE,
    persona=Persona("13", "2589846132", "Solo", "Pruebas", "Revisor Fiscal", "Jurídica"),
)


def _arbol(evento, ambiente=2):
    return etree.fromstring(ConstructorEvento(evento, software=SOFTWARE, ambiente=ambiente).generar_xml())


def _texto(arbol, ruta):
    return arbol.findtext(ruta, namespaces=NS)


class CudeEventoTests(SimpleTestCase):
    def test_vector_del_anexo(self):
        """Numeral 12.1.1.1 del anexo RADIAN 1.1."""
        cude = ident.calcular_cude_evento(
            numero_evento="1", fecha=date(2019, 4, 30), hora=time(19, 48, 50),
            nit_emisor_evento="99998888", id_receptor_evento="800197268",
            codigo_evento="030", numero_documento="FE123", tipo_documento="01",
            pin_software="11111",
        )
        self.assertEqual(
            cude,
            "0d91ba25b01f5e7dbda870a11b274501d3a62a73e91932c473c86c93f12a142a"
            "2ac45876efcde3e679024a01c0be41f9",
        )

    def test_el_del_xml_es_el_de_la_composicion(self):
        """La composición que el ejemplo 030 deja en su cbc:Note[1]."""
        arbol = _arbol(ACUSE)
        esperado = hashlib.sha384(
            b"ACR00212020-12-1218:30:37-05:00900373979900373972030SETG9800003580120191"
        ).hexdigest()
        self.assertEqual(_texto(arbol, "cbc:UUID"), esperado)


class ConstructorEventoTests(SimpleTestCase):
    def test_valida_contra_el_xsd(self):
        for evento in (
            ACUSE,
            replace(ACUSE, codigo="031", descripcion="Reclamo de la Factura Electrónica de Venta",
                    persona=None, concepto_reclamo=("02", "Mercancía no entregada totalmente")),
            replace(ACUSE, codigo="032", descripcion="Recibo del bien y/o prestación del servicio"),
            replace(ACUSE, codigo="033", descripcion="Aceptación expresa", persona=None),
        ):
            with self.subTest(evento=evento.codigo):
                arbol = _arbol(evento)
                if not ESQUEMA.validate(arbol):
                    self.fail(str(ESQUEMA.error_log))

    def test_cabecera(self):
        arbol = _arbol(ACUSE)
        self.assertEqual(etree.QName(arbol).localname, "ApplicationResponse")
        self.assertEqual(_texto(arbol, "cbc:UBLVersionID"), "UBL 2.1")
        self.assertEqual(_texto(arbol, "cbc:CustomizationID"), "1")
        self.assertEqual(
            _texto(arbol, "cbc:ProfileID"),
            "DIAN 2.1: ApplicationResponse de la Factura Electrónica de Venta",
        )
        self.assertEqual(_texto(arbol, "cbc:ProfileExecutionID"), "2")
        self.assertEqual(_texto(arbol, "cbc:ID"), "ACR0021")
        uuid = arbol.find("cbc:UUID", NS)
        self.assertEqual(uuid.get("schemeName"), "CUDE-SHA384")
        self.assertEqual(uuid.get("schemeID"), "2")
        self.assertEqual(_texto(arbol, "cbc:IssueDate"), "2020-12-12")
        self.assertEqual(_texto(arbol, "cbc:IssueTime"), "18:30:37-05:00")

    def test_extensiones_dian(self):
        arbol = _arbol(ACUSE)
        dian = arbol.find(".//sts:DianExtensions", NS)
        self.assertIsNone(dian.find("sts:InvoiceControl", NS))
        proveedor = dian.find("sts:SoftwareProvider/sts:ProviderID", NS)
        self.assertEqual(proveedor.text, "900373979")
        self.assertEqual(proveedor.get("schemeID"), "1")
        self.assertEqual(_texto(dian, "sts:SoftwareProvider/sts:SoftwareID"), SOFTWARE.identificador)
        # El mismo que trae el ejemplo oficial 030: SHA-384(SoftwareID + PIN + número).
        self.assertEqual(
            _texto(dian, "sts:SoftwareSecurityCode"),
            "55dfb0384c1d47d41568fbf9b5a5b92151b13490ea4d6cfc45d6b6b4256b81cd"
            "1987b188daa53d5f7211fd758eaa4a14",
        )
        self.assertEqual(
            _texto(dian, "sts:AuthorizationProvider/sts:AuthorizationProviderID"), "800197268",
        )
        self.assertEqual(
            _texto(dian, "sts:QRCode"),
            f"https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey={CUFE}",
        )

    def test_produccion(self):
        arbol = _arbol(ACUSE, ambiente=1)
        self.assertEqual(_texto(arbol, "cbc:ProfileExecutionID"), "1")
        self.assertTrue(_texto(arbol, ".//sts:QRCode").startswith("https://catalogo-vpfe.dian"))

    def test_partes(self):
        arbol = _arbol(ACUSE)
        emisor = arbol.find("cac:SenderParty/cac:PartyTaxScheme", NS)
        self.assertEqual(_texto(emisor, "cbc:RegistrationName"), "Pruebas")
        nit = emisor.find("cbc:CompanyID", NS)
        self.assertEqual(nit.text, "900373979")
        self.assertEqual(nit.get("schemeID"), "1")
        self.assertEqual(nit.get("schemeName"), "31")
        self.assertEqual(nit.get("schemeVersionID"), "1")
        self.assertEqual(_texto(emisor, "cac:TaxScheme/cbc:ID"), "01")
        self.assertEqual(
            _texto(arbol, "cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID"), "900373972",
        )

    def test_un_receptor_con_cedula_no_lleva_dv(self):
        evento = replace(ACUSE, receptor=Parte("Pedro Pérez", "72659841", "13", "2"))
        nit = _arbol(evento).find("cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID", NS)
        self.assertIsNone(nit.get("schemeID"))
        self.assertEqual(nit.get("schemeName"), "13")
        self.assertEqual(nit.get("schemeVersionID"), "2")

    def test_respuesta_y_referencia_a_la_factura(self):
        respuesta = _arbol(ACUSE).find("cac:DocumentResponse", NS)
        codigo = respuesta.find("cac:Response/cbc:ResponseCode", NS)
        self.assertEqual(codigo.text, "030")
        self.assertIsNone(codigo.get("listID"))
        self.assertEqual(
            _texto(respuesta, "cac:Response/cbc:Description"),
            "Acuse de recibo de Factura Electrónica de Venta",
        )
        self.assertEqual(_texto(respuesta, "cac:DocumentReference/cbc:ID"), "SETG980000358")
        cufe = respuesta.find("cac:DocumentReference/cbc:UUID", NS)
        self.assertEqual(cufe.text, CUFE)
        self.assertEqual(cufe.get("schemeName"), "CUFE-SHA384")
        self.assertEqual(_texto(respuesta, "cac:DocumentReference/cbc:DocumentTypeCode"), "01")

    def test_la_persona_que_recibe(self):
        persona = _arbol(ACUSE).find("cac:DocumentResponse/cac:IssuerParty/cac:Person", NS)
        documento = persona.find("cbc:ID", NS)
        self.assertEqual(documento.text, "2589846132")
        self.assertEqual(documento.get("schemeName"), "13")
        self.assertIsNone(documento.get("schemeID"))
        self.assertEqual(
            [etree.QName(e).localname for e in persona],
            ["ID", "FirstName", "FamilyName", "JobTitle", "OrganizationDepartment"],
        )
        self.assertEqual(_texto(persona, "cbc:JobTitle"), "Revisor Fiscal")

    def test_el_reclamo_lleva_su_concepto(self):
        evento = replace(
            ACUSE, codigo="031", descripcion="Reclamo de la Factura Electrónica de Venta",
            persona=None, concepto_reclamo=("02", "Mercancía no entregada totalmente"),
        )
        respuesta = _arbol(evento).find("cac:DocumentResponse", NS)
        codigo = respuesta.find("cac:Response/cbc:ResponseCode", NS)
        self.assertEqual(codigo.text, "031")
        self.assertEqual(codigo.get("listID"), "02")
        self.assertEqual(codigo.get("name"), "Mercancía no entregada totalmente")
        self.assertIsNone(respuesta.find("cac:IssuerParty", NS))

    def test_validaciones(self):
        casos = (
            (replace(ACUSE, persona=None), "lleva la persona"),
            (replace(ACUSE, codigo="032", persona=None), "lleva la persona"),
            (replace(ACUSE, codigo="031", persona=None), "lleva su concepto"),
            (replace(ACUSE, codigo="034"), "no soportado"),
        )
        for evento, mensaje in casos:
            with self.subTest(codigo=evento.codigo, mensaje=mensaje):
                with self.assertRaisesMessage(ValueError, mensaje):
                    ConstructorEvento(evento, software=SOFTWARE, ambiente=2)


class FirmaEventoTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llave, cls.cert = _generar_certificado()

    def _firmado(self):
        firmador = firma.FirmadorXAdES(
            self.llave, self.cert,
            policy_id=settings.DIAN_POLICY_ID, policy_hash="dGVzdGhhc2g=",
            signing_time=dt.datetime(2020, 12, 12, 18, 31, 0, tzinfo=firma.TZ_COLOMBIA),
        )
        xml = ConstructorEvento(ACUSE, software=SOFTWARE, ambiente=2).generar_xml()
        return etree.fromstring(firmador.firmar(xml))

    def test_firmado_valida_contra_el_xsd(self):
        arbol = self._firmado()
        if not ESQUEMA.validate(arbol):
            self.fail(str(ESQUEMA.error_log))
        extensiones = arbol.findall("ext:UBLExtensions/ext:UBLExtension", NS)
        self.assertEqual(len(extensiones), 2)
        self.assertIsNotNone(extensiones[1].find(".//ds:Signature", NS))

    def test_la_firma_verifica(self):
        arbol = self._firmado()
        signed_info = arbol.find(".//ds:SignedInfo", NS)
        valor = base64.b64decode(arbol.findtext(".//ds:SignatureValue", namespaces=NS))
        self.cert.public_key().verify(
            valor,
            etree.tostring(signed_info, method="c14n", exclusive=False),
            padding.PKCS1v15(), hashes.SHA256(),
        )
        # Y el digest del documento es el de la raíz sin la firma.
        referencia = arbol.find(".//ds:SignedInfo/ds:Reference[@URI='']", NS)
        firma_xml = arbol.find(".//ds:Signature", NS)
        firma_xml.getparent().remove(firma_xml)
        digest = base64.b64encode(hashlib.sha256(
            etree.tostring(arbol, method="c14n", exclusive=False)
        ).digest()).decode()
        self.assertEqual(referencia.findtext("ds:DigestValue", namespaces=NS), digest)
