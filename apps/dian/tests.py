"""Pruebas del cálculo de identificadores DIAN (CUFE/CUDE)."""
from datetime import date, time

from django.test import SimpleTestCase

from apps.dian import identificadores as ident


class FormatoValoresTests(SimpleTestCase):
    def test_trunca_no_redondea(self):
        # La DIAN exige truncar (hacia cero), no redondear.
        self.assertEqual(ident.formatear_valor("19.999"), "19.99")
        self.assertEqual(ident.formatear_valor("19.991"), "19.99")
        self.assertEqual(ident.formatear_valor(285000), "285000.00")
        self.assertEqual(ident.formatear_valor(0), "0.00")

    def test_float_sin_ruido_binario(self):
        self.assertEqual(ident.formatear_valor(235.28), "235.28")

    def test_formatear_fecha_y_hora(self):
        self.assertEqual(ident.formatear_fecha(date(2019, 1, 16)), "2019-01-16")
        self.assertEqual(ident.formatear_hora(time(10, 53, 10)), "10:53:10-05:00")


class CalculoCUFETests(SimpleTestCase):
    """Ejemplo oficial del Anexo Técnico v1.9, sección 11.2.1 (pág. 657)."""

    def test_cufe_ejemplo_oficial(self):
        cufe = ident.calcular_cufe(
            numero_factura="323200000129",
            fecha="2019-01-16",
            hora="10:53:10-05:00",
            valor_sin_impuestos="1500000.00",
            valor_iva="285000.00",
            valor_inc="0.00",
            valor_ica="0.00",
            valor_total="1785000.00",
            nit_emisor="700085371",
            id_adquirente="800199436",
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            tipo_ambiente="1",
        )
        self.assertEqual(
            cufe,
            "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33"
            "381030bcd4c3c3f156c506ed5908f9276f5bd9b4",
        )

    def test_cufe_acepta_objetos_fecha_hora_y_numeros(self):
        """Mismo resultado pasando date/time/Decimal en vez de strings."""
        cufe = ident.calcular_cufe(
            numero_factura="323200000129",
            fecha=date(2019, 1, 16),
            hora=time(10, 53, 10),
            valor_sin_impuestos=1500000,
            valor_iva=285000,
            valor_total=1785000,
            nit_emisor="700085371",
            id_adquirente="800199436",
            clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
            tipo_ambiente=1,
        )
        self.assertEqual(
            cufe,
            "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33"
            "381030bcd4c3c3f156c506ed5908f9276f5bd9b4",
        )


class CalculoCUDETests(SimpleTestCase):
    """Ejemplo oficial del Anexo Técnico v1.9, sección 11.4.1 (pág. 661)."""

    def test_cude_ejemplo_oficial(self):
        cude = ident.calcular_cude(
            numero_factura="8110007871",
            fecha="2019-02-20",
            hora="16:46:55-05:00",
            valor_sin_impuestos="235.28",
            valor_iva="19.00",
            valor_inc="0.00",
            valor_ica="8.28",
            valor_total="262.56",
            nit_emisor="900373076",
            id_adquirente="8355990",
            pin_software="12345",
            tipo_ambiente="2",
        )
        self.assertEqual(
            cude,
            "955327eb55f8bdf16d069358a063d87e1577a292cb088ec186ed60bb"
            "c38e750b7b3980659b278ead789b95f9c51a9ef7",
        )


class CodigoSeguridadSoftwareTests(SimpleTestCase):
    def test_es_sha384_de_la_concatenacion(self):
        import hashlib

        esperado = hashlib.sha384("idsw12345NUMFAC1".encode()).hexdigest()
        obtenido = ident.calcular_codigo_seguridad_software(
            id_software="idsw", pin="12345", numero_documento="NUMFAC1"
        )
        self.assertEqual(obtenido, esperado)
