"""Pruebas de la generación del XML UBL 2.1 (factura de venta)."""
from datetime import date, time
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.dian import ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian


class GeneracionUBLTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        c = crear_catalogos_minimos()
        cls.cat = c
        cls.factura = c["nit"].__class__  # no usado; placeholder

        cls.emisor = Emisor.objects.create(
            razon_social="Empresa Demo SAS",
            nombre_comercial="Demo",
            tipo_identificacion=c["nit"],
            numero_identificacion="700085371",
            digito_verificacion="1",
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
            correo="demo@empresa.co",
        )
        cls.software = SoftwareDian.objects.create(
            emisor=cls.emisor,
            identificador="56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
            pin="12345",
            id_proveedor="700085371",
        )
        from apps.catalogos.models import TipoFactura

        tipo_factura = TipoFactura.objects.create(codigo="01", nombre="Factura de Venta")
        cls.resolucion = ResolucionFacturacion.objects.create(
            emisor=cls.emisor,
            tipo_factura=tipo_factura,
            numero_resolucion="18760000001",
            fecha_resolucion=date(2019, 1, 19),
            prefijo="SETP",
            rango_desde=990000000,
            rango_hasta=995000000,
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            vigente_desde=date(2019, 1, 19),
            vigente_hasta=date(2030, 1, 19),
        )
        cls.adquirente = doc.Adquirente.objects.create(
            razon_social="Cliente Demo",
            tipo_identificacion=c["nit"],
            numero_identificacion="800199436",
            digito_verificacion="6",
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Cra 4 # 5-6",
        )
        cls.documento = doc.DocumentoElectronico.objects.create(
            tipo=doc.DocumentoElectronico.Tipo.FACTURA_VENTA,
            emisor=cls.emisor,
            resolucion=cls.resolucion,
            adquirente=cls.adquirente,
            prefijo="SETP",
            consecutivo=990000129,
            numero="323200000129",
            fecha_emision=date(2019, 1, 16),
            hora_emision=time(10, 53, 10),
            moneda=c["cop"],
            valor_bruto=Decimal("1500000.00"),
            total_impuestos=Decimal("285000.00"),
            total_a_pagar=Decimal("1785000.00"),
        )
        linea = doc.LineaDocumento.objects.create(
            documento=cls.documento,
            numero_linea=1,
            descripcion="Producto demo",
            codigo_producto="DEMO-1",
            cantidad=Decimal("1"),
            unidad_medida=c["unidad"],
            valor_unitario=Decimal("1500000"),
            valor_total=Decimal("1500000.00"),
        )
        doc.ImpuestoLinea.objects.create(
            linea=linea,
            tributo=c["iva"],
            base_gravable=Decimal("1500000.00"),
            tarifa=Decimal("19.00"),
            valor=Decimal("285000.00"),
        )

    def _generar(self, ambiente=2):
        return ubl.generar_xml_factura(
            self.documento,
            software=self.software,
            resolucion=self.resolucion,
            ambiente=ambiente,
            clave_tecnica=self.resolucion.clave_tecnica,
        )

    def test_xml_bien_formado_y_cufe(self):
        # El ejemplo oficial del Anexo se calculó con TipoAmbiente=1.
        xml = self._generar(ambiente=1)
        arbol = etree.fromstring(xml)
        ns = ubl.NS
        # El CUFE va en cbc:UUID y debe coincidir con el oficial.
        uuid = arbol.find(f"{{{ns['cbc']}}}UUID")
        self.assertEqual(uuid.get("schemeName"), "CUFE-SHA384")
        self.assertEqual(
            uuid.text,
            "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33"
            "381030bcd4c3c3f156c506ed5908f9276f5bd9b4",
        )

    def test_estructura_minima(self):
        xml = self._generar()
        arbol = etree.fromstring(xml)
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}ID"), "323200000129")
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}ProfileExecutionID"), "2")
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}InvoiceTypeCode"), "01")
        self.assertEqual(len(arbol.findall(f"{{{ns['cac']}}}InvoiceLine")), 1)
        # QR con la URL de habilitación.
        qr = arbol.find(f".//{{{ns['sts']}}}QRCode")
        self.assertIn("catalogo-vpfe-hab.dian.gov.co", qr.text)

    def test_valida_contra_xsd_oficial(self):
        xml = self._generar()
        xsd_path = settings.DIAN_XSD_DIR / "maindoc" / "UBL-Invoice-2.1.xsd"
        esquema = etree.XMLSchema(etree.parse(str(xsd_path)))
        arbol = etree.fromstring(xml)
        valido = esquema.validate(arbol)
        if not valido:
            self.fail("XML inválido contra XSD:\n" + str(esquema.error_log))
        self.assertTrue(valido)
