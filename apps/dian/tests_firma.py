"""Pruebas de la firma XAdES-EPES (verificación criptográfica independiente)."""
import base64
import re
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
from apps.emisores.models import Emisor, Resolucion, SoftwareDian


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
        )
        tipo = TipoFactura.objects.create(codigo="01", nombre="Factura de Venta")
        cls.resolucion = Resolucion.objects.create(
            emisor=emisor, tipo_factura=tipo, numero_resolucion="18760000001",
            fecha_resolucion=date(2019, 1, 19), prefijo="SETP",
            rango_desde=990000000, rango_hasta=995000000,
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19),
        )
        cls.documento = doc.Documento.objects.create(
            documento_tipo=doc.DocumentoTipo.objects.get(codigo=doc.DocumentoTipo.Codigo.FACTURA_VENTA), emisor=emisor,
            resolucion=cls.resolucion, prefijo="SETP",
            consecutivo=990000129, numero="323200000129",
            fecha_emision=date(2019, 1, 16), hora_emision=time(10, 53, 10),
            moneda=c["cop"], valor_bruto=Decimal("1500000.00"),
            total_impuestos=Decimal("285000.00"), total_a_pagar=Decimal("1785000.00"),
        )
        doc.Adquiriente.objects.create(
            documento=cls.documento,
            razon_social="Cliente Demo", tipo_identificacion=c["nit"],
            numero_identificacion="800199436", tipo_organizacion=c["juridica"],
            pais=c["colombia"],
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


class FirmaAisladaTests(FirmaXAdESTests):
    """Regresión del rechazo **ZE02** ("Valor de la firma inválido").

    La DIAN lo devolvía en nómina con una firma que verificaba bien en tres
    implementaciones independientes. La causa no era el cálculo sino los bytes
    transmitidos: el ``ds:SignedInfo`` se canonicaliza con C14N inclusiva, que
    emite *todas* las declaraciones de namespace en ámbito, y la firma heredaba
    de la raíz varias sin declararlas. Un validador que extrae el nodo
    ``ds:Signature`` y lo canonicaliza suelto pierde esas declaraciones y
    obtiene otro ``SignedInfo``.

    Estas pruebas fijan las dos mitades de la corrección: que la firma siga
    verificando **fuera** de su documento, y que escribir las declaraciones no
    haya movido ningún digest. Ver ``FirmadorXAdES._declarar_contexto_heredado``.
    """

    def _signature_suelta(self, xml):
        """El nodo ``ds:Signature`` extraído, serializado y vuelto a parsear.

        Es exactamente lo que hace el validador que provocaba el ZE02. Recibe
        el XML ya firmado en vez de firmarlo: cada firma lleva un UUID nuevo,
        así que dos llamadas a ``_firmar`` no son comparables entre sí.
        """
        sig = etree.fromstring(xml).find(f".//{{{ubl.NS['ds']}}}Signature")
        return etree.fromstring(etree.tostring(sig, encoding="UTF-8"))

    def test_la_firma_verifica_extraida_del_documento(self):
        suelta = self._signature_suelta(self._firmar())
        ns = ubl.NS
        signed_info = suelta.find(f"{{{ns['ds']}}}SignedInfo")
        firmado = base64.b64decode(suelta.find(f"{{{ns['ds']}}}SignatureValue").text)

        # No lanza si la firma sigue siendo válida sin el contexto de la raíz.
        self.cert.public_key().verify(
            firmado,
            etree.tostring(signed_info, method="c14n", exclusive=False),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_el_signed_info_canonicaliza_igual_dentro_y_fuera(self):
        """Es la razón por la que lo anterior funciona, dicha byte a byte."""
        xml = self._firmar()
        ns = ubl.NS
        dentro = etree.tostring(
            etree.fromstring(xml).find(f".//{{{ns['ds']}}}SignedInfo"),
            method="c14n", exclusive=False,
        )
        fuera = etree.tostring(
            self._signature_suelta(xml).find(f"{{{ns['ds']}}}SignedInfo"),
            method="c14n", exclusive=False,
        )
        self.assertEqual(dentro, fuera)

    def test_la_firma_declara_el_contexto_que_heredaba(self):
        """Las declaraciones están en los bytes, no solo resueltas en el árbol."""
        xml = self._firmar()
        apertura = xml[xml.find(b"<ds:Signature"):xml.find(b">", xml.find(b"<ds:Signature"))]

        arbol = etree.fromstring(xml)
        contenido = arbol.findall(f".//{{{ubl.NS['ext']}}}ExtensionContent")[-1]
        for prefijo in contenido.nsmap:
            if prefijo == "xml":
                continue
            esperado = b"xmlns=" if prefijo is None else f"xmlns:{prefijo}=".encode()
            self.assertIn(esperado, apertura)

    def test_la_declaracion_xml_usa_comillas_dobles(self):
        """Como los documentos aceptados; queda fuera de la canonicalización."""
        self.assertTrue(
            self._firmar().startswith(
                b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
            )
        )


class OrdenDeclaracionesNominaTests(TestCase):
    """Regresión del **ZE02**: la raíz debe copiar el orden del anexo.

    El validador de nómina de la DIAN rechazó quince documentos con "Valor de
    la firma inválido" cuyas firmas eran correctas. Lo único que los separaba de
    los aceptados era el **orden de las declaraciones de namespace y de los
    atributos** del elemento raíz, que canónicamente no significa nada —la C14N
    los ordena— pero que allí sí se mira.

    Estas pruebas fijan ese orden contra la **ejemplificación oficial** que está
    en el repositorio, de modo que si alguien vuelve a construir la raíz con
    `nsmap` (que es lo natural, y lo que fallaba) el test lo detecta sin gastar
    un envío del Set de Pruebas. Ver ``docs/anexo-nomina.md`` §9 bis.
    """

    RUTA_EJEMPLO = "apps/dian/datos/ejemplos/nomina/nomina-individual.xml"

    def _declaraciones(self, xml: bytes) -> list[str]:
        """Los nombres de las declaraciones y atributos de la raíz, en orden.

        Se leen de los bytes y no del árbol a propósito: es justo el orden que
        el árbol no conserva y que aquí importa.
        """
        apertura = xml[:xml.index(b">", xml.index(b"<Nomina"))].decode("utf-8")
        return re.findall(r'\s(xmlns(?::[\w]+)?|SchemaLocation|xsi:schemaLocation)=', apertura)

    def test_la_raiz_copia_el_orden_de_la_ejemplificacion_oficial(self):
        from apps.dian import nomina as xml_nomina

        with open(self.RUTA_EJEMPLO, "rb") as fh:
            oficial = self._declaraciones(fh.read().lstrip(b"\xef\xbb\xbf"))

        raiz = xml_nomina.ConstructorNominaXML._raiz(
            _ConstructorFalso(xml_nomina.ConstructorNominaXML)
        )
        nuestro = self._declaraciones(etree.tostring(raiz, encoding="UTF-8"))

        self.assertEqual(
            nuestro, oficial,
            "El orden de la raíz dejó de coincidir con la ejemplificación oficial. "
            "La DIAN rechaza con ZE02 cuando difiere; ver docs/anexo-nomina.md §9 bis.",
        )

    def test_el_namespace_por_defecto_va_primero(self):
        """Lo más fácil de romper al reintroducir un `nsmap`."""
        from apps.dian import nomina as xml_nomina

        raiz = xml_nomina.ConstructorNominaXML._raiz(
            _ConstructorFalso(xml_nomina.ConstructorNominaXML)
        )
        self.assertEqual(self._declaraciones(etree.tostring(raiz, encoding="UTF-8"))[0], "xmlns")

    def test_schemalocation_va_antes_que_xsi_schemalocation(self):
        from apps.dian import nomina as xml_nomina

        raiz = xml_nomina.ConstructorNominaXML._raiz(
            _ConstructorFalso(xml_nomina.ConstructorNominaXML)
        )
        nombres = self._declaraciones(etree.tostring(raiz, encoding="UTF-8"))
        self.assertLess(nombres.index("SchemaLocation"), nombres.index("xsi:schemaLocation"))


class _ConstructorFalso:
    """Lo mínimo que `_raiz` necesita: el nombre y el namespace de la raíz.

    Evita montar una nómina entera en la base para comprobar una cadena.
    """

    def __init__(self, clase):
        self.nombre_raiz = clase.nombre_raiz
        self.ns_raiz = clase.ns_raiz
