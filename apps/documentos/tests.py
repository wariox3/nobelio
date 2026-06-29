"""Pruebas de integración del modelo de documentos."""
from datetime import date, time
from decimal import Decimal

from django.test import TestCase

from apps.catalogos import models as cat
from apps.dian import identificadores as ident
from apps.documentos import models as doc
from apps.emisores.models import Emisor

from apps.documentos.tests_utils import crear_catalogos_minimos


class GrafoDocumentoTests(TestCase):
    """Crea el grafo emisor → documento → línea → impuesto y calcula el CUFE."""

    @classmethod
    def setUpTestData(cls):
        c = crear_catalogos_minimos()
        cls.cat = c

        cls.emisor = Emisor.objects.create(
            cuenta=c["cuenta"],
            razon_social="Empresa Demo SAS",
            tipo_identificacion=c["nit"],
            numero_identificacion="700085371",
            digito_verificacion="1",
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )
        cls.adquirente = doc.Adquiriente.objects.create(
            razon_social="Cliente Demo",
            tipo_identificacion=c["nit"],
            numero_identificacion="800199436",
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
        )

    def test_crear_documento_con_linea_e_impuesto(self):
        documento = doc.DocumentoElectronico.objects.create(
            tipo=doc.DocumentoElectronico.Tipo.FACTURA_VENTA,
            emisor=self.emisor,
            adquiriente=self.adquirente,
            prefijo="SETP",
            consecutivo=129,
            numero="SETP990000129",
            fecha_emision=date(2019, 1, 16),
            hora_emision=time(10, 53, 10),
            moneda=self.cat["cop"],
            valor_bruto=Decimal("1500000.00"),
            total_impuestos=Decimal("285000.00"),
            total_a_pagar=Decimal("1785000.00"),
        )
        linea = doc.LineaDocumento.objects.create(
            documento=documento,
            numero_linea=1,
            descripcion="Producto demo",
            cantidad=Decimal("1"),
            unidad_medida=self.cat["unidad"],
            valor_unitario=Decimal("1500000"),
            valor_total=Decimal("1500000.00"),
        )
        doc.ImpuestoLinea.objects.create(
            linea=linea,
            tributo=self.cat["iva"],
            base_gravable=Decimal("1500000.00"),
            tarifa=Decimal("19.00"),
            valor=Decimal("285000.00"),
        )

        self.assertEqual(documento.lineas.count(), 1)
        self.assertEqual(linea.impuestos.count(), 1)
        self.assertEqual(str(documento), "Factura de venta SETP990000129")

    def test_cufe_a_partir_del_documento(self):
        """El CUFE calculado desde los datos del documento coincide con el oficial."""
        cufe = ident.calcular_cufe(
            numero_factura="323200000129",
            fecha=date(2019, 1, 16),
            hora=time(10, 53, 10),
            valor_sin_impuestos=Decimal("1500000.00"),
            valor_iva=Decimal("285000.00"),
            valor_total=Decimal("1785000.00"),
            nit_emisor=self.emisor.numero_identificacion,
            id_adquirente=self.adquirente.numero_identificacion,
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            tipo_ambiente="1",
        )
        self.assertEqual(
            cufe,
            "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33"
            "381030bcd4c3c3f156c506ed5908f9276f5bd9b4",
        )
