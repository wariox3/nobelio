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
        # WS-Security en layout estricto: Timestamp, BinarySecurityToken, Signature.
        seguridad = arbol.find(f".//{{{ns['wsse']}}}Security")
        self.assertNotIn(f"{{{ns['soap']}}}mustUnderstand", seguridad.attrib)
        self.assertEqual(
            [etree.QName(h).localname for h in seguridad],
            ["Timestamp", "BinarySecurityToken", "Signature"],
        )
        # El certificado se referencia por huella SHA-1 (RequireThumbprintReference).
        key_id = arbol.find(f".//{{{ns['wsse']}}}KeyIdentifier")
        self.assertIsNotNone(key_id)
        self.assertEqual(key_id.get("ValueType"), soap.VALUE_TYPE_THUMBPRINT)
        # El Body lleva los parámetros de la operación (no se firma; lo protege TLS).
        body = arbol.find(f"{{{ns['soap']}}}Body")
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

        # Solo cuentan las Reference de la firma (las del SignedInfo), no la de KeyInfo.
        signed_info = arbol.find(f".//{{{ns['ds']}}}SignedInfo")
        refs = signed_info.findall(f"{{{ns['ds']}}}Reference")
        valores = {r.get("URI"): r.find(f"{{{ns['ds']}}}DigestValue").text for r in refs}

        wsu_id = f"{{{ns['wsu']}}}Id"
        timestamp = arbol.find(f".//{{{ns['wsu']}}}Timestamp")
        to = arbol.find(f".//{{{ns['wsa']}}}To")
        # Se firman el Timestamp y el wsa:To (SignedParts de la política DIAN).
        self.assertEqual(valores["#" + timestamp.get(wsu_id)], digest(timestamp))
        self.assertEqual(valores["#" + to.get(wsu_id)], digest(to))


class RespuestaDianTests(SimpleTestCase):
    def test_parsea_zipkey_de_set_pruebas(self):
        xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><SendTestSetAsyncResponse xmlns="http://wcf.dian.colombia">
            <SendTestSetAsyncResult xmlns:a="x"><a:ZipKey>track-123</a:ZipKey>
            </SendTestSetAsyncResult></SendTestSetAsyncResponse></s:Body></s:Envelope>"""
        r = soap.RespuestaDian.desde_xml(xml)
        self.assertEqual(r.track_id, "track-123")

    def test_extrae_fecha_validacion_del_application_response(self):
        import base64
        app_response = (
            '<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"'
            ' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
            "<cbc:IssueDate>2026-06-29</cbc:IssueDate>"
            "<cbc:IssueTime>14:30:05-05:00</cbc:IssueTime>"
            "</ApplicationResponse>"
        )
        b64 = base64.b64encode(app_response.encode()).decode()
        xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><GetStatusResponse xmlns="http://wcf.dian.colombia">
            <GetStatusResult xmlns:a="x">
              <a:IsValid>true</a:IsValid>
              <a:StatusCode>00</a:StatusCode>
              <a:XmlBase64Bytes>{b64}</a:XmlBase64Bytes>
            </GetStatusResult></GetStatusResponse></s:Body></s:Envelope>"""
        r = soap.RespuestaDian.desde_xml(xml)
        self.assertTrue(r.es_valido)
        self.assertIsNotNone(r.fecha_validacion)
        self.assertEqual(r.fecha_validacion.year, 2026)
        self.assertEqual(r.fecha_validacion.hour, 14)

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


class RangoNumeracionTests(SimpleTestCase):
    XML = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
      <s:Body><GetNumberingRangeResponse xmlns="http://wcf.dian.colombia">
        <GetNumberingRangeResult xmlns:a="http://schemas.datacontract.org/2004/07/x">
          <a:OperationCode>100</a:OperationCode>
          <a:ResponseList>
            <a:NumberRangeResponse>
              <a:ResolutionNumber>18760000001</a:ResolutionNumber>
              <a:ResolutionDate>2019-01-10</a:ResolutionDate>
              <a:Prefix>SETP</a:Prefix>
              <a:FromNumber>990000000</a:FromNumber>
              <a:ToNumber>995000000</a:ToNumber>
              <a:ValidDateFrom>2019-01-10</a:ValidDateFrom>
              <a:ValidDateTo>2020-01-10</a:ValidDateTo>
              <a:TechnicalKey>fc8eac422eba16e22ffd8c6f94b3f40a6e38162c</a:TechnicalKey>
            </a:NumberRangeResponse>
            <a:NumberRangeResponse>
              <a:ResolutionNumber>18760000002</a:ResolutionNumber>
              <a:Prefix></a:Prefix>
              <a:FromNumber>1</a:FromNumber>
              <a:ToNumber>5000</a:ToNumber>
              <a:TechnicalKey>aa11bb22</a:TechnicalKey>
            </a:NumberRangeResponse>
          </a:ResponseList>
        </GetNumberingRangeResult></GetNumberingRangeResponse></s:Body></s:Envelope>"""

    def test_parsea_lista_de_rangos(self):
        rangos = soap.RangoNumeracion.lista_desde_xml(self.XML)
        self.assertEqual(len(rangos), 2)

        primero = rangos[0]
        self.assertEqual(primero.prefijo, "SETP")
        self.assertEqual(primero.numero_resolucion, "18760000001")
        self.assertEqual(primero.fecha_resolucion, "2019-01-10")
        self.assertEqual(primero.rango_desde, 990000000)
        self.assertEqual(primero.rango_hasta, 995000000)
        self.assertEqual(primero.clave_tecnica, "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c")

        # Rango sin prefijo ni fechas: no debe romper el parseo.
        self.assertEqual(rangos[1].prefijo, "")
        self.assertEqual(rangos[1].rango_desde, 1)

    def test_respuesta_vacia(self):
        xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><GetNumberingRangeResponse xmlns="http://wcf.dian.colombia">
            <GetNumberingRangeResult/></GetNumberingRangeResponse></s:Body></s:Envelope>"""
        self.assertEqual(soap.RangoNumeracion.lista_desde_xml(xml), [])

    def test_respuesta_rangos_incluye_codigo_y_descripcion(self):
        resp = soap.RespuestaRangos.desde_xml(self.XML)
        self.assertEqual(resp.codigo, "100")
        self.assertEqual(len(resp.rangos), 2)

    def test_respuesta_rangos_sin_prefijos(self):
        # Caso real: software sin prefijos asociados (OperationCode 302, lista nil).
        xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><GetNumberingRangeResponse xmlns="http://wcf.dian.colombia">
            <GetNumberingRangeResult xmlns:b="http://schemas.datacontract.org/2004/07/NumberRangeResponseList"
                                     xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
              <b:OperationCode>302</b:OperationCode>
              <b:OperationDescription>No registra prefijos asociados al codigo de software: abc.</b:OperationDescription>
              <b:ResponseList i:nil="true"/>
            </GetNumberingRangeResult></GetNumberingRangeResponse></s:Body></s:Envelope>"""
        resp = soap.RespuestaRangos.desde_xml(xml)
        self.assertEqual(resp.codigo, "302")
        self.assertIn("No registra prefijos", resp.descripcion)
        self.assertEqual(resp.rangos, [])
