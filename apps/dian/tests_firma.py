"""Pruebas de la firma XAdES-EPES (verificación criptográfica independiente)."""
import base64
import datetime as dt
import hashlib
from datetime import date, time
from decimal import Decimal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.dian import firma, ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian


def _generar_certificado():
    """Crea una llave RSA y un certificado autofirmado para pruebas."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Empresa Demo SAS"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Empresa Demo SAS"),
    ])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    return llave, cert


class FirmaXAdESTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        c = crear_catalogos_minimos()
        from apps.catalogos.models import TipoFactura

        emisor = Emisor.objects.create(
            cuenta=c["cuenta"],
            razon_social="Empresa Demo SAS",
            tipo_identificacion=c["nit"], numero_identificacion="700085371",
            digito_verificacion="1", tipo_organizacion=c["juridica"],
            pais=c["colombia"], departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )
        cls.software = SoftwareDian.objects.create(
            emisor=emisor, identificador="id-sw-demo", pin="12345",
            id_proveedor="700085371",
        )
        tipo = TipoFactura.objects.create(codigo="01", nombre="Factura de Venta")
        cls.resolucion = ResolucionFacturacion.objects.create(
            emisor=emisor, tipo_factura=tipo, numero_resolucion="18760000001",
            fecha_resolucion=date(2019, 1, 19), prefijo="SETP",
            rango_desde=990000000, rango_hasta=995000000,
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19),
        )
        adq = doc.Adquiriente.objects.create(
            razon_social="Cliente Demo", tipo_identificacion=c["nit"],
            numero_identificacion="800199436", tipo_organizacion=c["juridica"],
            pais=c["colombia"],
        )
        cls.documento = doc.Documento.objects.create(
            tipo=doc.Documento.Tipo.FACTURA_VENTA, emisor=emisor,
            resolucion=cls.resolucion, adquiriente=adq, prefijo="SETP",
            consecutivo=990000129, numero="323200000129",
            fecha_emision=date(2019, 1, 16), hora_emision=time(10, 53, 10),
            moneda=c["cop"], valor_bruto=Decimal("1500000.00"),
            total_impuestos=Decimal("285000.00"), total_a_pagar=Decimal("1785000.00"),
        )
        linea = doc.DocumentoDetalle.objects.create(
            documento=cls.documento, numero_linea=1, descripcion="Producto demo",
            cantidad=Decimal("1"), unidad_medida=c["unidad"],
            valor_unitario=Decimal("1500000"), valor_total=Decimal("1500000.00"),
        )
        doc.DocumentoDetalleImpuesto.objects.create(
            detalle=linea, tributo=c["iva"], base_gravable=Decimal("1500000.00"),
            tarifa=Decimal("19.00"), valor=Decimal("285000.00"),
        )
        cls.llave, cls.cert = _generar_certificado()

    def _firmar(self):
        xml = ubl.generar_xml_factura(
            self.documento, software=self.software, resolucion=self.resolucion,
            ambiente=2, clave_tecnica=self.resolucion.clave_tecnica,
        )
        firmador = firma.FirmadorXAdES(
            self.llave, self.cert,
            policy_id=settings.DIAN_POLICY_ID,
            policy_hash="dGVzdGhhc2g=",  # hash de prueba
            signing_time=dt.datetime(2019, 1, 16, 10, 53, 10, 123000, tzinfo=firma.TZ_COLOMBIA),
        )
        return firmador.firmar(xml)

    def test_estructura_firma_presente(self):
        arbol = etree.fromstring(self._firmar())
        ns = ubl.NS
        # Hay dos UBLExtension; la segunda contiene la firma.
        extensiones = arbol.findall(f".//{{{ns['ext']}}}UBLExtension")
        self.assertEqual(len(extensiones), 2)
        sig = arbol.find(f".//{{{ns['ds']}}}Signature")
        self.assertIsNotNone(sig)
        self.assertEqual(len(sig.findall(f"{{{ns['ds']}}}SignedInfo/{{{ns['ds']}}}Reference")), 3)
        self.assertIsNotNone(arbol.find(f".//{{{ns['xades']}}}SignaturePolicyIdentifier"))

    def test_signature_value_valida_criptograficamente(self):
        arbol = etree.fromstring(self._firmar())
        ns = ubl.NS
        signed_info = arbol.find(f".//{{{ns['ds']}}}SignedInfo")
        sig_value = arbol.find(f".//{{{ns['ds']}}}SignatureValue").text
        firmado = base64.b64decode(sig_value)
        # No lanza excepción si la firma es válida.
        self.cert.public_key().verify(
            firmado,
            etree.tostring(signed_info, method="c14n", exclusive=False),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_digests_de_referencias_correctos(self):
        arbol = etree.fromstring(self._firmar())
        ns = ubl.NS

        def digest(elem):
            canon = etree.tostring(elem, method="c14n", exclusive=False)
            return base64.b64encode(hashlib.sha256(canon).digest()).decode()

        refs = arbol.findall(f".//{{{ns['ds']}}}SignedInfo/{{{ns['ds']}}}Reference")
        valores = {r.get("URI"): r.find(f"{{{ns['ds']}}}DigestValue").text for r in refs}

        # Ref KeyInfo
        key_info = arbol.find(f".//{{{ns['ds']}}}KeyInfo")
        uri_ki = f"#{key_info.get('Id')}"
        self.assertEqual(valores[uri_ki], digest(key_info))

        # Ref SignedProperties
        sp = arbol.find(f".//{{{ns['xades']}}}SignedProperties")
        uri_sp = f"#{sp.get('Id')}"
        self.assertEqual(valores[uri_sp], digest(sp))

        # Ref documento (enveloped): quitar la firma y canonicalizar la raíz.
        sig = arbol.find(f".//{{{ns['ds']}}}Signature")
        sig.getparent().remove(sig)
        self.assertEqual(valores[""], digest(arbol))

    def test_xml_firmado_valida_contra_xsd(self):
        xml = self._firmar()
        xsd_path = settings.DIAN_XSD_DIR / "maindoc" / "UBL-Invoice-2.1.xsd"
        esquema = etree.XMLSchema(etree.parse(str(xsd_path)))
        arbol = etree.fromstring(xml)
        if not esquema.validate(arbol):
            self.fail("XML firmado inválido contra XSD:\n" + str(esquema.error_log))
