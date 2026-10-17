"""Pruebas de generación UBL de notas crédito/débito y documento soporte."""
from datetime import date, time
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.catalogos.models import Tributo
from apps.dian import ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_adquiriente, crear_documento_factura


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

    def _crear_nota(self, codigo, numero, consecutivo):
        nota = doc.Documento.objects.create(
            ambiente=2,
            documento_tipo=doc.DocumentoTipo.objects.get(codigo=codigo),
            emisor=self.base["emisor"],
            documento_referencia=self.factura, prefijo="NC", consecutivo=consecutivo,
            numero=numero, fecha_emision=date(2024, 2, 1), hora_emision=time(9, 0, 0),
            moneda=self.base["catalogos"]["cop"], valor_bruto=Decimal("100000.00"),
            total_impuestos=Decimal("19000.00"), total_a_pagar=Decimal("119000.00"),
            observaciones="Devolución parcial",
        )
        crear_adquiriente(nota, self.base["catalogos"])
        linea = doc.DocumentoDetalle.objects.create(
            documento=nota, numero_linea=1, descripcion="Devolución producto",
            cantidad=Decimal("1"), unidad_medida=self.base["catalogos"]["unidad"],
            valor_unitario=Decimal("100000"), valor_total=Decimal("100000.00"),
        )
        doc.DocumentoDetalleImpuesto.objects.create(
            detalle=linea, tributo=self.base["catalogos"]["iva"],
            base_gravable=Decimal("100000.00"), tarifa=Decimal("19.00"),
            valor=Decimal("19000.00"),
        )
        return nota


class NotaCreditoTests(NotasTestBase):
    def _xml(self):
        nota = self._crear_nota(doc.DocumentoTipo.Codigo.NOTA_CREDITO, "NC1", 1)
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


class NotaSinReferenciaTests(NotasTestBase):
    """Sin documento referenciado: «sin referencia a facturas» (22 y 32)."""

    def _arbol(self, codigo, numero):
        nota = self._crear_nota(codigo, numero, 1)
        nota.documento_referencia = None
        nota.concepto_correccion = "4"
        nota.save(update_fields=["documento_referencia", "concepto_correccion"])
        xml = ubl.constructor_para(nota, software=self.base["software"], ambiente=2).generar_xml()
        return xml, etree.fromstring(xml)

    def test_nota_credito_sale_como_22_sin_billing_reference(self):
        xml, arbol = self._arbol(doc.DocumentoTipo.Codigo.NOTA_CREDITO, "NC2")
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}CustomizationID"), "22")
        self.assertIsNone(arbol.find(f"{{{ns['cac']}}}BillingReference"))
        disc = arbol.find(f"{{{ns['cac']}}}DiscrepancyResponse")
        self.assertIsNone(disc.find(f"{{{ns['cbc']}}}ReferenceID"))
        self.assertEqual(disc.findtext(f"{{{ns['cbc']}}}ResponseCode"), "4")
        esquema, arbol = _validar_xsd(xml, "UBL-CreditNote-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("CreditNote inválida:\n" + str(esquema.error_log))

    def test_nota_debito_sale_como_32_sin_billing_reference(self):
        xml, arbol = self._arbol(doc.DocumentoTipo.Codigo.NOTA_DEBITO, "ND2")
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}CustomizationID"), "32")
        self.assertIsNone(arbol.find(f"{{{ns['cac']}}}BillingReference"))
        self.assertIsNotNone(arbol.find(f"{{{ns['cac']}}}DiscrepancyResponse"))
        esquema, arbol = _validar_xsd(xml, "UBL-DebitNote-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("DebitNote inválida:\n" + str(esquema.error_log))

    def test_con_referencia_sigue_saliendo_como_20(self):
        nota = self._crear_nota(doc.DocumentoTipo.Codigo.NOTA_CREDITO, "NC3", 1)
        arbol = etree.fromstring(
            ubl.constructor_para(nota, software=self.base["software"], ambiente=2).generar_xml()
        )
        self.assertEqual(arbol.findtext(f"{{{ubl.NS['cbc']}}}CustomizationID"), "20")


class NotaDebitoTests(NotasTestBase):
    def _xml(self):
        nota = self._crear_nota(doc.DocumentoTipo.Codigo.NOTA_DEBITO, "ND1", 1)
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
    def _xml(self, impuestos=(), **linea_extra):
        ds = doc.Documento.objects.create(
            ambiente=2,
            documento_tipo=doc.DocumentoTipo.objects.get(codigo=doc.DocumentoTipo.Codigo.DOCUMENTO_SOPORTE),
            emisor=self.base["emisor"],
            prefijo="DS", consecutivo=1, numero="DS1",
            fecha_emision=date(2024, 2, 1), hora_emision=time(9, 0, 0),
            moneda=self.base["catalogos"]["cop"], valor_bruto=Decimal("50000.00"),
            total_impuestos=Decimal("0.00"), total_a_pagar=Decimal("50000.00"),
        )
        crear_adquiriente(ds, self.base["catalogos"])
        linea = doc.DocumentoDetalle.objects.create(
            documento=ds, numero_linea=1, descripcion="Compra a no obligado",
            cantidad=Decimal("1"), unidad_medida=self.base["catalogos"]["unidad"],
            valor_unitario=Decimal("50000"), valor_total=Decimal("50000.00"),
            **linea_extra,
        )
        for tributo, tarifa, valor in impuestos:
            doc.DocumentoDetalleImpuesto.objects.create(
                detalle=linea, tributo=tributo, base_gravable=Decimal("50000.00"),
                tarifa=Decimal(tarifa), valor=Decimal(valor),
            )
        return ubl.constructor_para(ds, software=self.base["software"], ambiente=2).generar_xml()

    def test_las_retenciones_van_aparte_de_los_impuestos(self):
        """El IVA en cac:TaxTotal y la ReteFuente en cac:WithholdingTaxTotal.

        Un documento soporte con impuestos reventaba al generar el XML: el
        reparto consulta los códigos de retención y el import de `Tributo`
        había quedado en otro módulo. La otra prueba no lo veía porque su
        documento no trae impuestos.
        """
        retefuente = Tributo.objects.create(id=6, codigo="06", nombre="ReteFuente")
        xml = self._xml(impuestos=[
            (self.base["catalogos"]["iva"], "19.00", "9500.00"),
            (retefuente, "10.00", "5000.00"),
        ])

        arbol = etree.fromstring(xml)
        ns = ubl.NS

        def codigos(etiqueta):
            return [
                esquema.findtext(f"{{{ns['cbc']}}}ID")
                for esquema in arbol.findall(
                    f"{{{ns['cac']}}}{etiqueta}/{{{ns['cac']}}}TaxSubtotal/"
                    f"{{{ns['cac']}}}TaxCategory/{{{ns['cac']}}}TaxScheme"
                )
            ]

        self.assertEqual(codigos("TaxTotal"), ["01"])
        self.assertEqual(codigos("WithholdingTaxTotal"), ["06"])

        # En el documento y en la línea, la 06 se llama como en la lista del DS
        # y no como en el catálogo, que sale de la de factura: con «ReteFuente»
        # la DIAN rechaza (DSAT13 y DSAY13).
        nombres = [
            esquema.findtext(f"{{{ns['cbc']}}}Name")
            for esquema in arbol.iter(f"{{{ns['cac']}}}TaxScheme")
            if esquema.findtext(f"{{{ns['cbc']}}}ID") == "06"
        ]
        self.assertEqual(nombres, ["ReteRenta", "ReteRenta"])
        esquema, arbol = _validar_xsd(xml, "UBL-Invoice-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("Documento soporte inválido:\n" + str(esquema.error_log))

    def _periodo(self, xml):
        ns = ubl.NS
        periodo = etree.fromstring(xml).find(
            f"{{{ns['cac']}}}InvoiceLine/{{{ns['cac']}}}InvoicePeriod"
        )
        return {
            etiqueta: periodo.findtext(f"{{{ns['cbc']}}}{etiqueta}")
            for etiqueta in ("StartDate", "EndDate", "DescriptionCode", "Description")
        }

    def test_sin_forma_de_generacion_la_linea_va_por_operacion(self):
        """La DIAN rechaza la línea sin ella (DSFC03 y DSFC04)."""
        self.assertEqual(self._periodo(self._xml()), {
            "StartDate": "2024-02-01", "EndDate": None,
            "DescriptionCode": "1", "Description": "Por operación",
        })

    def test_la_descripcion_de_la_forma_de_generacion_sale_de_la_lista(self):
        """No la que traiga la línea: la DIAN la compara con su tabla."""
        xml = self._xml(
            periodo_desde=date(2024, 1, 22), periodo_hasta=date(2024, 1, 28),
            periodo_descripcion_codigo="2", periodo_descripcion="Semana 4",
        )

        self.assertEqual(self._periodo(xml), {
            "StartDate": "2024-01-22", "EndDate": "2024-01-28",
            "DescriptionCode": "2", "Description": "Acumulado semanal",
        })
        esquema, arbol = _validar_xsd(xml, "UBL-Invoice-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("Documento soporte inválido:\n" + str(esquema.error_log))

    def test_tipo_05_y_cuds_valida_xsd(self):
        xml = self._xml()
        arbol = etree.fromstring(xml)
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}InvoiceTypeCode"), "05")
        # CUDS, no CUDE: el documento soporte tiene su propia composición de
        # hash y su propio schemeName (anexo del DS, numeral 4). La prueba
        # afirmaba CUDE, de cuando el DS reutilizaba el identificador de la
        # factura.
        self.assertEqual(arbol.find(f"{{{ns['cbc']}}}UUID").get("schemeName"), "CUDS-SHA384")
        esquema, arbol = _validar_xsd(xml, "UBL-Invoice-2.1.xsd")
        if not esquema.validate(arbol):
            self.fail("Documento soporte inválido:\n" + str(esquema.error_log))
