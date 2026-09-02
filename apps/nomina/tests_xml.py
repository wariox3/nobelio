"""Pruebas del XML de nómina: CUNE, XSD y sellado del ambiente.

El orden de las declaraciones de la raíz —la corrección del ZE02— no se prueba
aquí sino en `apps.dian.tests_firma.OrdenDeclaracionesNominaTests`, que lo fija
contra la ejemplificación oficial. Es la misma familia de regresión y no tiene
sentido tenerla en dos sitios.
"""
import hashlib

from django.conf import settings
from django.test import TestCase
from lxml import etree

from apps.dian import identificadores as ident
from apps.dian import nomina as xml_nomina
from apps.nomina.tests_utils import crear_emisor_de_nomina, crear_nomina


def _validar_xsd(xml: bytes, nombre_xsd: str):
    ruta = settings.DIAN_XSD_DIR / "nomina" / nombre_xsd
    esquema = etree.XMLSchema(etree.parse(str(ruta)))
    return esquema, etree.fromstring(xml)


class CuneTests(TestCase):
    """La composición del CUNE, fijada campo a campo.

    **No hay vector de prueba oficial.** El ejemplo del anexo (numeral 8.1.1.3)
    no reproduce su propio hash: se probó la composición documentada y todas las
    permutaciones de los once campos, y ninguna da el CUNE publicado. Así que el
    ancla real es otra: la nómina **NESETP6**, que la DIAN aceptó el 2026-08-30,
    y cuyo CUNE guardado reproduce esta misma función con sus datos. Se comprobó
    el 2026-09-02.

    Esa nómina no se puede traer aquí como caso porque en el CUNE entra el PIN
    del software, que es un secreto y no va al repositorio. Lo que sí se puede
    fijar —y es lo que se rompería si alguien reordena los campos o cambia un
    formato— es la composición: se arma a mano, en el orden del anexo, y se
    compara con lo que devuelve la función.
    """

    DATOS = {
        "numero_documento": "NESETP6",
        "fecha": "2026-08-30",
        "hora": "15:00:49-05:00",
        "valor_devengado": "1000000.00",
        "valor_deduccion": "80000.00",
        "valor_total": "920000.00",
        "nit_empleador": "901192048",
        "documento_empleado": "901192048",
        "tipo_xml": "102",
        "pin_software": "0000",   # no es el real; ver el docstring
        "tipo_ambiente": "2",
    }

    def test_la_composicion_es_la_de_los_once_campos_en_orden(self):
        from datetime import date, time

        esperado = hashlib.sha384(
            "".join([
                self.DATOS["numero_documento"],
                self.DATOS["fecha"],
                self.DATOS["hora"],
                self.DATOS["valor_devengado"],
                self.DATOS["valor_deduccion"],
                self.DATOS["valor_total"],
                self.DATOS["nit_empleador"],
                self.DATOS["documento_empleado"],
                self.DATOS["tipo_xml"],
                self.DATOS["pin_software"],
                self.DATOS["tipo_ambiente"],
            ]).encode("utf-8")
        ).hexdigest()

        obtenido = ident.calcular_cune(
            numero_documento="NESETP6",
            fecha=date(2026, 8, 30),
            hora=time(15, 0, 49),
            valor_devengado="1000000.00",
            valor_deduccion="80000.00",
            valor_total="920000.00",
            nit_empleador="901192048",
            documento_empleado="901192048",
            tipo_xml="102",
            pin_software="0000",
            tipo_ambiente=2,
        )
        self.assertEqual(obtenido, esperado)

    def test_la_hora_lleva_el_huso_de_bogota(self):
        """Sin el ``-05:00`` el hash cambia, y el documento se rechaza."""
        from datetime import time

        self.assertEqual(ident.formatear_hora(time(15, 0, 49)), "15:00:49-05:00")

    def test_los_valores_van_truncados_a_dos_decimales(self):
        """Truncados, no redondeados: es lo que hace el CUFE y lo que valida la DIAN."""
        from decimal import Decimal

        self.assertEqual(ident.formatear_valor(Decimal("1000000.005")), "1000000.00")
        self.assertEqual(ident.formatear_valor(Decimal("920000")), "920000.00")

    def test_el_ambiente_entra_en_el_cune(self):
        """Pruebas y producción no pueden dar el mismo identificador."""
        from datetime import date, time

        comunes = dict(
            numero_documento="NESETP6", fecha=date(2026, 8, 30), hora=time(15, 0, 49),
            valor_devengado="1000000.00", valor_deduccion="80000.00",
            valor_total="920000.00", nit_empleador="901192048",
            documento_empleado="901192048", tipo_xml="102", pin_software="0000",
        )
        self.assertNotEqual(
            ident.calcular_cune(**comunes, tipo_ambiente=1),
            ident.calcular_cune(**comunes, tipo_ambiente=2),
        )

    def test_el_tipo_de_xml_distingue_la_nomina_de_su_nota(self):
        """``102`` nómina y ``103`` nota de ajuste: mismos datos, distinto CUNE."""
        from datetime import date, time

        comunes = dict(
            numero_documento="NESETP6", fecha=date(2026, 8, 30), hora=time(15, 0, 49),
            valor_devengado="1000000.00", valor_deduccion="80000.00",
            valor_total="920000.00", nit_empleador="901192048",
            documento_empleado="901192048", pin_software="0000", tipo_ambiente=2,
        )
        self.assertNotEqual(
            ident.calcular_cune(**comunes, tipo_xml="102"),
            ident.calcular_cune(**comunes, tipo_xml="103"),
        )


class NominaXMLTests(TestCase):
    """El XML que se construye, contra el XSD oficial de la DIAN."""

    @classmethod
    def setUpTestData(cls):
        cls.base = crear_emisor_de_nomina()
        cls.nomina, _ = crear_nomina(cls.base)

    def _xml(self, nomina=None):
        return xml_nomina.generar_xml_nomina(
            nomina or self.nomina,
            software=self.base["software"],
            ambiente=2,
            pin=self.base["software"].pin,
        )

    def _q(self, etiqueta):
        """El nombre cualificado: la nómina va en un namespace por defecto."""
        return f"{{{xml_nomina.ConstructorNominaXML.ns_raiz}}}{etiqueta}"

    def test_la_raiz_y_los_datos_generales(self):
        arbol = etree.fromstring(self._xml())
        self.assertEqual(etree.QName(arbol).localname, "NominaIndividual")
        general = arbol.find(self._q("InformacionGeneral"))
        self.assertEqual(general.get("TipoXML"), "102")
        self.assertEqual(general.get("Ambiente"), "2")
        self.assertEqual(general.get("EncripCUNE"), "CUNE-SHA384")
        self.assertEqual(len(general.get("CUNE")), 96)

    def test_los_totales_salen_con_dos_decimales(self):
        arbol = etree.fromstring(self._xml())
        self.assertEqual(arbol.findtext(self._q("DevengadosTotal")), "1000000.00")
        self.assertEqual(arbol.findtext(self._q("DeduccionesTotal")), "80000.00")
        self.assertEqual(arbol.findtext(self._q("ComprobanteTotal")), "920000.00")

    def test_el_qr_apunta_al_cune(self):
        arbol = etree.fromstring(self._xml())
        cune = arbol.find(self._q("InformacionGeneral")).get("CUNE")
        self.assertIn(cune, arbol.findtext(self._q("CodigoQR")))

    def test_el_xml_firmado_valida_contra_el_xsd(self):
        """Se valida el firmado, que es el que se envía.

        El XML sin firmar no valida: el XSD exige al menos una `UBLExtension` y
        el constructor la deja vacía a propósito, porque quien la rellena es el
        firmador. Las ejemplificaciones oficiales tienen el mismo hueco.
        """
        from apps.dian import firma
        from apps.dian.tests_firma import _generar_certificado

        llave, cert = _generar_certificado()
        firmador = firma.FirmadorXAdES(
            llave, cert, policy_id="x", policy_hash="aGFzaA==",
        )
        esquema, arbol = _validar_xsd(
            firmador.firmar(self._xml()), "NominaIndividualElectronicaXSDV1.0.6.xsd"
        )
        if not esquema.validate(arbol):
            self.fail("Nómina inválida contra el XSD:\n" + str(esquema.error_log))
