"""Pruebas del cliente SOAP DIAN (empaquetado, WS-Security y parseo)."""
import base64
import hashlib
import io
import zipfile

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from django.test import SimpleTestCase
from lxml import etree

from apps.dian import soap
from apps.dian.tests_firma import _generar_certificado


class EmpaquetadoTests(SimpleTestCase):
    def test_zip_contiene_el_xml(self):
        contenido = b"<Invoice>demo</Invoice>"
        zip_bytes = soap.empaquetar_zip("fac.xml", contenido)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            self.assertEqual(zf.namelist(), ["fac.xml"])
            self.assertEqual(zf.read("fac.xml"), contenido)

    def test_base64_decodifica_a_zip_valido(self):
        b64 = soap.empaquetar_base64("fac.xml", b"<x/>")
        zip_bytes = base64.b64decode(b64)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(zip_bytes)))


class SobreSOAPTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llave, cls.cert = _generar_certificado()

    def _cliente(self):
        return soap.ClienteDian(
            "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc",
            self.llave, self.cert,
        )

    def _sobre(self):
        return self._cliente().construir_sobre(
            "SendTestSetAsync",
            {"fileName": "fac.zip", "contentFile": "QkFTRTY0", "testSetId": "abc"},
        )

    def test_estructura_del_sobre(self):
        arbol = etree.fromstring(self._sobre())
        ns = soap.NS
        self.assertEqual(
            arbol.findtext(f"{{{ns['soap']}}}Header/{{{ns['wsa']}}}Action"),
            "http://wcf.dian.colombia/IWcfDianCustomerServices/SendTestSetAsync",
        )
        # WS-Security con Timestamp, BinarySecurityToken y Signature.
        self.assertIsNotNone(arbol.find(f".//{{{ns['wsu']}}}Timestamp"))
        self.assertIsNotNone(arbol.find(f".//{{{ns['wsse']}}}BinarySecurityToken"))
        self.assertIsNotNone(arbol.find(f".//{{{ns['ds']}}}Signature"))
        # El Body lleva wsu:Id y los parámetros de la operación.
        body = arbol.find(f"{{{ns['soap']}}}Body")
        self.assertIn(f"{{{ns['wsu']}}}Id", body.attrib)
        self.assertEqual(
            body.findtext(f"{{{ns['wcf']}}}SendTestSetAsync/{{{ns['wcf']}}}testSetId"),
            "abc",
        )

    def test_firma_wssecurity_valida_criptograficamente(self):
        arbol = etree.fromstring(self._sobre())
        ns = soap.NS
        signed_info = arbol.find(f".//{{{ns['ds']}}}SignedInfo")
        sig_value = arbol.find(f".//{{{ns['ds']}}}SignatureValue").text
        self.cert.public_key().verify(
            base64.b64decode(sig_value),
            etree.tostring(signed_info, method="c14n", exclusive=True),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_digests_de_referencias_correctos(self):
        arbol = etree.fromstring(self._sobre())
        ns = soap.NS

        def digest(elem):
            canon = etree.tostring(elem, method="c14n", exclusive=True)
            return base64.b64encode(hashlib.sha256(canon).digest()).decode()

        refs = arbol.findall(f".//{{{ns['ds']}}}Reference")
        valores = {r.get("URI"): r.find(f"{{{ns['ds']}}}DigestValue").text for r in refs}

        wsu_id = f"{{{ns['wsu']}}}Id"
        timestamp = arbol.find(f".//{{{ns['wsu']}}}Timestamp")
        body = arbol.find(f"{{{ns['soap']}}}Body")
        self.assertEqual(valores["#" + timestamp.get(wsu_id)], digest(timestamp))
        self.assertEqual(valores["#" + body.get(wsu_id)], digest(body))


class RespuestaDianTests(SimpleTestCase):
    def test_parsea_zipkey_de_set_pruebas(self):
        xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><SendTestSetAsyncResponse xmlns="http://wcf.dian.colombia">
            <SendTestSetAsyncResult xmlns:a="x"><a:ZipKey>track-123</a:ZipKey>
            </SendTestSetAsyncResult></SendTestSetAsyncResponse></s:Body></s:Envelope>"""
        r = soap.RespuestaDian.desde_xml(xml)
        self.assertEqual(r.track_id, "track-123")

    def test_parsea_respuesta_con_estado_y_errores(self):
        xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><GetStatusResponse xmlns="http://wcf.dian.colombia">
            <GetStatusResult xmlns:a="x" xmlns:b="y">
              <a:IsValid>true</a:IsValid>
              <a:StatusCode>00</a:StatusCode>
              <a:StatusDescription>Procesado Correctamente</a:StatusDescription>
              <a:ErrorMessage><b:string>Advertencia 1</b:string></a:ErrorMessage>
            </GetStatusResult></GetStatusResponse></s:Body></s:Envelope>"""
        r = soap.RespuestaDian.desde_xml(xml)
        self.assertTrue(r.es_valido)
        self.assertEqual(r.codigo_estado, "00")
        self.assertEqual(r.descripcion_estado, "Procesado Correctamente")
        self.assertEqual(r.errores, ["Advertencia 1"])
