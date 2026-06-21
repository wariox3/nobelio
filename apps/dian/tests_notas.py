"""Pruebas de generación UBL de notas crédito/débito y documento soporte."""
from datetime import date, time
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.dian import ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_documento_factura


def _validar_xsd(xml: bytes, nombre_xsd: str):
    ruta = settings.DIAN_XSD_DIR / "maindoc" / nombre_xsd
    esquema = etree.XMLSchema(etree.parse(str(ruta)))
    arbol = etree.fromstring(xml)
    return esquema, arbol


class NotasTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.base = datos
        cls.factura = datos["documento"]
        cls.factura.cufe_cude = "f" * 96  # CUFE de la factura referenciada
        cls.factura.save(update_fields=["cufe_cude"])

    def _crear_nota(self, tipo, numero, consecutivo):
        nota = doc.DocumentoElectronico.objects.create(
            tipo=tipo, emisor=self.base["emisor"], adquirente=self.base["adquirente"],
            documento_referencia=self.factura, prefijo="NC", consecutivo=consecutivo,
            numero=numero, fecha_emision=date(2024, 2, 1), hora_emision=time(9, 0, 0),
            moneda=self.base["catalogos"]["cop"], valor_bruto=Decimal("100000.00"),
            total_impuestos=Decimal("19000.00"), total_a_pagar=Decimal("119000.00"),
            observaciones="Devolución parcial",
        )
        linea = doc.LineaDocumento.objects.create(
            documento=nota, numero_linea=1, descripcion="Devolución producto",
            cantidad=Decimal("1"), unidad_medida=self.base["catalogos"]["unidad"],
            valor_unitario=Decimal("100000"), valor_total=Decimal("100000.00"),
        )
        doc.ImpuestoLinea.objects.create(
            linea=linea, tributo=self.base["catalogos"]["iva"],
            base_gravable=Decimal("100000.00"), tarifa=Decimal("19.00"),
            valor=Decimal("19000.00"),
        )
        return nota


class NotaCreditoTests(NotasTestBase):
    def _xml(self):
        nota = self._crear_nota(doc.DocumentoElectronico.Tipo.NOTA_CREDITO, "NC1", 1)
        return ubl.constructor_para(
            nota, software=self.base["software"], ambiente=2,
        ).generar_xml()

    def test_raiz_y_cude(self):
        arbol = etree.fromstring(self._xml())
        self.assertEqual(etree.QName(arbol).localname, "CreditNote")
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}CreditNoteTypeCode"), "91")
        uuid = arbol.find(f"{{{ns['cbc']}}}UUID")
        self.assertEqual(uuid.get("schemeName"), "CUDE-SHA384")
        self.assertEqual(len(uuid.text), 96)

    def test_referencias_a_factura(self):
        arbol = etree.fromstring(self._xml())
        ns = ubl.NS
        disc = arbol.find(f"{{{ns['cac']}}}DiscrepancyResponse")
        self.assertEqual(disc.findtext(f"{{{ns['cbc']}}}ReferenceID"), "323200000129")
        billing = arbol.find(f".//{{{ns['cac']}}}InvoiceDocumentReference")
        self.assertEqual(billing.findtext(f"{{{ns['cbc']}}}ID"), "323200000129")
        self.assertEqual(len(arbol.findall(f"{{{ns['cac']}}}CreditNoteLine")), 1)

    def test_valida_contra_xsd(self):
        esquema, arbol = _validar_xsd(self._xml(), "UBL-CreditNote-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("CreditNote inválida:\n" + str(esquema.error_log))


class NotaDebitoTests(NotasTestBase):
    def _xml(self):
        nota = self._crear_nota(doc.DocumentoElectronico.Tipo.NOTA_DEBITO, "ND1", 1)
        return ubl.constructor_para(
            nota, software=self.base["software"], ambiente=2,
        ).generar_xml()

    def test_raiz_total_y_linea(self):
        arbol = etree.fromstring(self._xml())
        ns = ubl.NS
        self.assertEqual(etree.QName(arbol).localname, "DebitNote")
        # El UBL DebitNote no tiene elemento de tipo; el tipo se expresa por CustomizationID.
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}CustomizationID"), "30")
        # Débito usa RequestedMonetaryTotal y DebitNoteLine/DebitedQuantity.
        self.assertIsNotNone(arbol.find(f"{{{ns['cac']}}}RequestedMonetaryTotal"))
        linea = arbol.find(f"{{{ns['cac']}}}DebitNoteLine")
        self.assertIsNotNone(linea.find(f"{{{ns['cbc']}}}DebitedQuantity"))

    def test_valida_contra_xsd(self):
        esquema, arbol = _validar_xsd(self._xml(), "UBL-DebitNote-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("DebitNote inválida:\n" + str(esquema.error_log))


class DocumentoSoporteTests(NotasTestBase):
    def _xml(self):
        ds = doc.DocumentoElectronico.objects.create(
            tipo=doc.DocumentoElectronico.Tipo.DOCUMENTO_SOPORTE,
            emisor=self.base["emisor"], adquirente=self.base["adquirente"],
            prefijo="DS", consecutivo=1, numero="DS1",
            fecha_emision=date(2024, 2, 1), hora_emision=time(9, 0, 0),
            moneda=self.base["catalogos"]["cop"], valor_bruto=Decimal("50000.00"),
            total_impuestos=Decimal("0.00"), total_a_pagar=Decimal("50000.00"),
        )
        doc.LineaDocumento.objects.create(
            documento=ds, numero_linea=1, descripcion="Compra a no obligado",
            cantidad=Decimal("1"), unidad_medida=self.base["catalogos"]["unidad"],
            valor_unitario=Decimal("50000"), valor_total=Decimal("50000.00"),
        )
        return ubl.constructor_para(ds, software=self.base["software"], ambiente=2).generar_xml()

    def test_tipo_05_y_cude_valida_xsd(self):
        xml = self._xml()
        arbol = etree.fromstring(xml)
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}InvoiceTypeCode"), "05")
        self.assertEqual(arbol.find(f"{{{ns['cbc']}}}UUID").get("schemeName"), "CUDE-SHA384")
        esquema, arbol = _validar_xsd(xml, "UBL-Invoice-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("Documento soporte inválido:\n" + str(esquema.error_log))
