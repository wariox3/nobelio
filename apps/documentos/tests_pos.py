"""Pruebas del documento equivalente P.O.S. (Invoice tipo 20, CUDE).

Tres cosas, que son las que la DIAN rechaza si se rompen: las cinco extensiones
obligatorias (DEPD11 y DEPD21), la nomenclatura de archivo del numeral 8.13.5
—que es una tercera convención, distinta de la de factura y de la de nómina— y
el consecutivo de archivos, hexadecimal y reiniciado cada año.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from lxml import etree

from apps.dian import identificadores as ident
from apps.dian import ubl
from apps.documentos import models as doc
from apps.documentos.tests_utils import crear_adquiriente, crear_documento_factura
from apps.emisores.models import SoftwareDian


class NombreArchivoDocumentoEquivalenteTests(TestCase):
    """La nomenclatura del numeral 8.13.5, contra el ejemplo del anexo.

    Aquí sí hay vector oficial: `ds08001972680002000000001.xml`. Se descompone
    en prefijo `ds`, NIT `0800197268` a diez dígitos, código de proveedor
    tecnológico `000`, año `20` y consecutivo `00000001` en hexadecimal.
    """

    def test_reproduce_el_ejemplo_del_anexo(self):
        self.assertEqual(
            ident.nombre_archivo_documento_equivalente(
                "ds", nit="800197268", codigo_pt="", anio=2020, consecutivo=1,
            ),
            "ds08001972680002000000001",
        )

    def test_el_consecutivo_va_en_hexadecimal(self):
        """Hexadecimal, no decimal: es lo que separa esta convención de la de factura."""
        nombre = ident.nombre_archivo_documento_equivalente(
            "ds", nit="800197268", codigo_pt="007", anio=2026, consecutivo=255,
        )
        self.assertTrue(nombre.endswith("000000FF"))
        self.assertIn("007", nombre)

    def test_el_nit_se_rellena_a_diez_digitos_y_el_ppp_a_tres(self):
        nombre = ident.nombre_archivo_documento_equivalente(
            "ncs", nit="12345", codigo_pt="7", anio=2026, consecutivo=1,
        )
        # ncs + 0000012345 + 007 + 26 + 00000001
        self.assertEqual(nombre, "ncs00000123450072600000001")

    def test_cada_prefijo_tiene_el_suyo(self):
        """``ds`` el documento, ``ncs`` la nota de ajuste, ``z`` el zip."""
        comunes = dict(nit="800197268", codigo_pt="000", anio=2026, consecutivo=1)
        self.assertTrue(
            ident.nombre_archivo_documento_equivalente("ds", **comunes).startswith("ds")
        )
        self.assertTrue(
            ident.nombre_archivo_documento_equivalente("ncs", **comunes).startswith("ncs")
        )
        self.assertTrue(
            ident.nombre_archivo_documento_equivalente("z", **comunes).startswith("z")
        )


class ConsecutivoArchivoTests(TestCase):
    """El contador de archivos enviados: por emisor y por año."""

    @classmethod
    def setUpTestData(cls):
        cls.base = crear_documento_factura()
        cls.emisor = cls.base["emisor"]

    def test_empieza_en_uno_y_avanza(self):
        from apps.documentos.models import ConsecutivoArchivoDocumentoEquivalente as C

        primero = C.siguiente(self.emisor, 2026)
        segundo = C.siguiente(self.emisor, 2026)
        self.assertEqual((primero, segundo), (1, 2))

    def test_se_reinicia_cada_ano(self):
        """El anexo lo dice: vuelve a ``00000001`` cada 1 de enero."""
        from apps.documentos.models import ConsecutivoArchivoDocumentoEquivalente as C

        C.siguiente(self.emisor, 2026)
        C.siguiente(self.emisor, 2026)
        self.assertEqual(C.siguiente(self.emisor, 2027), 1)

    def test_es_de_cada_emisor(self):
        from apps.documentos.models import ConsecutivoArchivoDocumentoEquivalente as C
        from apps.emisores.models import Emisor

        otro = Emisor.objects.create(
            cuenta=self.base["catalogos"]["cuenta"],
            razon_social="Otro SAS", tipo_identificacion=self.base["catalogos"]["nit"],
            numero_identificacion="800199436", digito_verificacion="6",
            tipo_organizacion=self.base["catalogos"]["juridica"],
            pais=self.base["catalogos"]["colombia"],
            departamento=self.base["catalogos"]["antioquia"],
            municipio=self.base["catalogos"]["medellin"], direccion="Calle 9",
        )
        C.siguiente(self.emisor, 2026)
        self.assertEqual(C.siguiente(otro, 2026), 1)


class ExtensionesPOSTests(TestCase):
    """Las cinco extensiones obligatorias (DEPD11 y DEPD21).

    Sin condicionales: un tiquete sin el programa de puntos se rechaza igual
    que uno sin firma. Aquí se comprueban las tres propias del P.O.S.; el
    `sts:DianExtensions` viene del constructor común y la `ds:Signature` la
    añade el firmador.
    """

    @classmethod
    def setUpTestData(cls):
        base = crear_documento_factura()
        cls.catalogos = base["catalogos"]
        cls.emisor = base["emisor"]
        cls.software = SoftwareDian.objects.create(
            emisor=cls.emisor, tipo=SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE,
            identificador="pos-software-id", pin="54321",
            codigo_proveedor_tecnologico="007",
        )
        cls.tiquete = doc.Documento.objects.create(
            documento_tipo=doc.DocumentoTipo.objects.get(
                codigo=doc.DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS
            ),
            emisor=cls.emisor, prefijo="POS", consecutivo=1, numero="POS1",
            fecha_emision=date(2026, 9, 1), hora_emision="10:00:00",
            moneda=cls.catalogos["cop"], valor_bruto=Decimal("50000.00"),
            total_impuestos=Decimal("9500.00"), total_a_pagar=Decimal("59500.00"),
        )
        crear_adquiriente(cls.tiquete, cls.catalogos)
        doc.DocumentoDetalle.objects.create(
            documento=cls.tiquete, numero_linea=1, descripcion="Producto",
            cantidad=Decimal("1"), unidad_medida=cls.catalogos["unidad"],
            valor_unitario=Decimal("50000"), valor_total=Decimal("50000.00"),
        )
        doc.DocumentoPOS.objects.create(
            documento=cls.tiquete,
            caja_placa="CAJA-01", caja_ubicacion="Pasillo 3",
            caja_tipo="Caja de apoyo", cajero="Ana Gómez",
            codigo_venta="V-2026-0001",
        )

    def _arbol(self):
        xml = ubl.constructor_para(
            self.tiquete, software=self.software, ambiente=2
        ).generar_xml()
        return etree.fromstring(xml)

    def _extension(self, arbol, grupo):
        """El nodo de grupo de una extensión, por su nombre local.

        Se busca por todo el árbol y no bajo `ExtensionContent`: el grupo es
        nieto suyo —cuelga de un envoltorio (`FabricanteSoftware`,
        `BeneficiosComprador`, `PuntoVenta`)— y va en el namespace de la raíz,
        no en uno propio.
        """
        for nodo in arbol.iter():
            if etree.QName(nodo).localname == grupo:
                return nodo
        return None

    def _pares(self, extension):
        """Los pares ``Name``/``Value``, que van planos y alternados.

        No son nodos anidados: el grupo lleva `Name`, `Value`, `Name`, `Value`…
        como hermanos, tal cual la ejemplificación oficial.
        """
        hijos = list(extension)
        return {
            hijos[i].text: hijos[i + 1].text
            for i in range(0, len(hijos) - 1, 2)
        }

    def test_el_tipo_y_el_identificador_son_los_del_pos(self):
        arbol = self._arbol()
        ns = ubl.NS
        self.assertEqual(arbol.findtext(f"{{{ns['cbc']}}}InvoiceTypeCode"), "20")
        # CUDE y no CUFE: se firma con el PIN del software, no con la clave
        # técnica de una resolución.
        self.assertEqual(
            arbol.find(f"{{{ns['cbc']}}}UUID").get("schemeName"), "CUDE-SHA384"
        )
        self.assertIn("Documento Equivalente POS", arbol.findtext(f"{{{ns['cbc']}}}ProfileID"))

    def test_estan_las_tres_extensiones_propias(self):
        arbol = self._arbol()
        for nombre in (
            "InformacionDelFabricanteDelSoftware",
            "InformacionBeneficiosComprador",
            "InformacionCajaVenta",
        ):
            self.assertIsNotNone(
                self._extension(arbol, nombre),
                f"Falta la extensión {nombre}; DEPD11/DEPD21 la rechazan.",
            )

    def test_la_caja_sale_con_los_literales_acentuados(self):
        """``UbicaciónCaja`` y ``CódigoVenta`` llevan tilde: es lo que compara la DIAN."""
        pares = self._pares(self._extension(self._arbol(), "InformacionCajaVenta"))
        self.assertEqual(pares["PlacaCaja"], "CAJA-01")
        self.assertEqual(pares["UbicaciónCaja"], "Pasillo 3")
        self.assertEqual(pares["Cajero"], "Ana Gómez")
        self.assertEqual(pares["TipoCaja"], "Caja de apoyo")
        self.assertEqual(pares["CódigoVenta"], "V-2026-0001")

    def test_el_subtotal_vacio_cae_al_valor_bruto(self):
        """Se admite informarlo aparte, pero si no viene se emite el del documento."""
        pares = self._pares(self._extension(self._arbol(), "InformacionCajaVenta"))
        self.assertEqual(pares["SubTotal"], "50000.00")

    def test_los_beneficios_caen_al_adquiriente_si_no_se_informan(self):
        pares = self._pares(
            self._extension(self._arbol(), "InformacionBeneficiosComprador")
        )
        self.assertEqual(pares["Codigo"], self.tiquete.adquiriente.numero_identificacion)
        self.assertEqual(
            pares["NombresApellidos"], self.tiquete.adquiriente.razon_social
        )

    def test_el_fabricante_del_emisor_gana_al_de_la_plataforma(self):
        """`SoftwareDian` guarda los tres campos como excepción por emisor."""
        self.software.fabricante_nombre = "Fulano"
        self.software.fabricante_razon_social = "Software Propio SAS"
        self.software.fabricante_nombre_software = "MiPOS"
        self.software.save()

        pares = self._pares(
            self._extension(self._arbol(), "InformacionDelFabricanteDelSoftware")
        )
        self.assertEqual(pares["NombreApellido"], "Fulano")
        self.assertEqual(pares["RazonSocial"], "Software Propio SAS")
        self.assertEqual(pares["NombreSoftware"], "MiPOS")
