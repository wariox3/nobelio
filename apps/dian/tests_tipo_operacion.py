"""Pruebas del tipo de operación de la factura en el XML (``cbc:CustomizationID``).

Estándar (10), AIU (09) y transporte de carga (12). Referencia: anexo FE 1.9,
regla FAV03 (AIU) y numeral 19.2 (transporte); resumen en
``docs/anexo-tecnico.md``, sección 11.
"""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.catalogos.carga import cargar
from apps.catalogos.models import TipoOperacion
from apps.dian import ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_adquiriente, crear_documento_factura

CBC = ubl.NS["cbc"]
CAC = ubl.NS["cac"]
NOTA_AIU = "Contrato de servicios AIU por concepto de: obra civil bodega 3"
REMESA = {
    "radicado": 37796060, "consecutivo": "REM12345", "valor_flete": 45000,
    "cantidad": 2500, "unidad": "KGM",
}


def _esquema():
    return etree.XMLSchema(etree.parse(
        str(settings.DIAN_XSD_DIR / "maindoc" / "UBL-Invoice-2.1.xsd")
    ))


class TipoOperacionXMLTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar([TipoOperacion])
        cls.datos = crear_documento_factura()
        cls.factura = cls.datos["documento"]
        cls.linea = cls.factura.detalles.get()

    def _con_tipo(self, codigo):
        self.factura.tipo_operacion = (
            TipoOperacion.objects.get(codigo=codigo) if codigo else None
        )
        self.factura.save(update_fields=["tipo_operacion"])

    def _segunda_linea(self, remesa=None):
        """Una línea más, sin impuestos: un servicio adicional si no trae remesa."""
        return doc.DocumentoDetalle.objects.create(
            documento=self.factura, numero_linea=2, descripcion="Cargue",
            codigo_producto="CARGUE", cantidad=Decimal("1"),
            unidad_medida=self.datos["catalogos"]["unidad"],
            valor_unitario=Decimal("50000"), valor_total=Decimal("50000.00"),
            remesa=remesa,
        )

    def _generar(self):
        d = self.datos
        xml = ubl.constructor_para(
            self.factura, software=d["software"], resolucion=d["resolucion"],
            ambiente=2, clave_tecnica=d["resolucion"].clave_tecnica,
        ).generar_xml()
        arbol = etree.fromstring(xml)
        esquema = _esquema()
        if not esquema.validate(arbol):
            self.fail("XML inválido contra XSD:\n" + str(esquema.error_log))
        return arbol

    @staticmethod
    def _lineas(arbol):
        return arbol.findall(f"{{{CAC}}}InvoiceLine")

    @staticmethod
    def _propiedades(linea):
        return [
            (
                p.findtext(f"{{{CBC}}}Name"),
                p.findtext(f"{{{CBC}}}Value"),
                p.find(f"{{{CBC}}}ValueQuantity"),
            )
            for p in linea.iter(f"{{{CAC}}}AdditionalItemProperty")
        ]

    # --- Estándar: lo que ya se emitía no cambia -----------------------------

    def test_sin_tipo_de_operacion_sale_estandar(self):
        arbol = self._generar()

        self.assertEqual(arbol.findtext(f"{{{CBC}}}CustomizationID"), "10")

    def test_el_estandar_explicito_sale_igual_que_sin_tipo(self):
        sin_tipo = etree.tostring(self._generar())
        self._con_tipo(TipoOperacion.ESTANDAR)

        self.assertEqual(etree.tostring(self._generar()), sin_tipo)

    def test_la_linea_estandar_no_lleva_marcas_de_transporte(self):
        linea = self._lineas(self._generar())[0]

        self.assertIsNone(linea.find(f"{{{CBC}}}ID").get("schemeID"))
        self.assertEqual(self._propiedades(linea), [])

    def test_una_remesa_fuera_de_transporte_no_llega_al_xml(self):
        """La API la rechaza; si aun así estuviera en la base, no se emite."""
        self.linea.remesa = REMESA
        self.linea.save(update_fields=["remesa"])

        linea = self._lineas(self._generar())[0]

        self.assertIsNone(linea.find(f"{{{CBC}}}ID").get("schemeID"))
        self.assertEqual(self._propiedades(linea), [])

    # --- AIU -------------------------------------------------------------

    def test_aiu_sale_con_su_codigo_y_la_nota_de_la_linea(self):
        self._con_tipo(TipoOperacion.AIU)
        self.linea.nota = NOTA_AIU
        self.linea.save(update_fields=["nota"])

        arbol = self._generar()

        self.assertEqual(arbol.findtext(f"{{{CBC}}}CustomizationID"), "09")
        self.assertEqual(self._lineas(arbol)[0].findtext(f"{{{CBC}}}Note"), NOTA_AIU)

    # --- Transporte de carga ----------------------------------------------

    def test_transporte_sale_con_su_codigo(self):
        self._con_tipo(TipoOperacion.TRANSPORTE)
        self.linea.remesa = REMESA
        self.linea.save(update_fields=["remesa"])

        arbol = self._generar()

        self.assertEqual(arbol.findtext(f"{{{CBC}}}CustomizationID"), "12")

    def test_la_linea_de_remesa_lleva_schemeid_1_y_sus_tres_propiedades(self):
        self._con_tipo(TipoOperacion.TRANSPORTE)
        self.linea.remesa = REMESA
        self.linea.save(update_fields=["remesa"])

        linea = self._lineas(self._generar())[0]

        self.assertEqual(linea.find(f"{{{CBC}}}ID").get("schemeID"), "1")
        propiedades = self._propiedades(linea)
        self.assertEqual(
            [(nombre, valor) for nombre, valor, _ in propiedades],
            [("01", "37796060"), ("02", "REM12345"), ("03", "45000")],
        )
        self.assertIsNone(propiedades[0][2])
        self.assertIsNone(propiedades[1][2])
        cantidad = propiedades[2][2]
        self.assertEqual(cantidad.text, "2500")
        self.assertEqual(cantidad.get("unitCode"), "KGM")

    def test_las_propiedades_van_al_final_del_item(self):
        """En el XSD, AdditionalItemProperty sigue a StandardItemIdentification."""
        self._con_tipo(TipoOperacion.TRANSPORTE)
        self.linea.remesa = REMESA
        self.linea.save(update_fields=["remesa"])

        item = self._lineas(self._generar())[0].find(f"{{{CAC}}}Item")

        hijos = [etree.QName(hijo).localname for hijo in item]
        self.assertEqual(hijos[-4:], [
            "StandardItemIdentification", "AdditionalItemProperty",
            "AdditionalItemProperty", "AdditionalItemProperty",
        ])

    def test_el_servicio_adicional_lleva_schemeid_0_y_ninguna_propiedad(self):
        self._con_tipo(TipoOperacion.TRANSPORTE)
        self.linea.remesa = REMESA
        self.linea.save(update_fields=["remesa"])
        self._segunda_linea()

        _, adicional = self._lineas(self._generar())

        self.assertEqual(adicional.find(f"{{{CBC}}}ID").get("schemeID"), "0")
        self.assertEqual(self._propiedades(adicional), [])

    def test_la_unidad_en_galones(self):
        self._con_tipo(TipoOperacion.TRANSPORTE)
        self.linea.remesa = {**REMESA, "unidad": "GLL"}
        self.linea.save(update_fields=["remesa"])

        linea = self._lineas(self._generar())[0]

        self.assertEqual(self._propiedades(linea)[2][2].get("unitCode"), "GLL")

    # --- Lo que no es factura no se toca ---------------------------------

    def test_la_nota_credito_no_marca_sus_lineas(self):
        """El gancho de la línea es de la factura: la nota sigue igual."""
        nota = doc.Documento.objects.create(
            ambiente=2,
            documento_tipo=doc.DocumentoTipo.objects.get(
                codigo=doc.DocumentoTipo.Codigo.NOTA_CREDITO
            ),
            emisor=self.datos["emisor"], prefijo="NC", consecutivo=1, numero="NC1",
            fecha_emision=self.factura.fecha_emision,
            hora_emision=self.factura.hora_emision, moneda=self.factura.moneda,
            concepto_correccion="2",
        )
        doc.DocumentoDetalle.objects.create(
            documento=nota, numero_linea=1, descripcion="Flete", codigo_producto="F",
            cantidad=Decimal("1"), unidad_medida=self.datos["catalogos"]["unidad"],
            valor_unitario=Decimal("100"), valor_total=Decimal("100.00"), remesa=REMESA,
        )
        crear_adquiriente(nota, self.datos["catalogos"])

        constructor = ubl.constructor_para(
            nota, software=self.datos["software"], ambiente=2,
        )
        arbol = etree.fromstring(constructor.generar_xml())

        linea = arbol.find(f"{{{CAC}}}CreditNoteLine")
        self.assertIsNone(linea.find(f"{{{CBC}}}ID").get("schemeID"))
        self.assertEqual(self._propiedades(linea), [])
        self.assertEqual(arbol.findtext(f"{{{CBC}}}CustomizationID"), "22")
