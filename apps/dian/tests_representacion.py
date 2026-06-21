"""Pruebas de la representación gráfica PDF + QR."""
import io

from django.test import SimpleTestCase, TestCase
from pypdf import PdfReader

from apps.dian import representacion
from apps.documentos.tests_utils import crear_documento_factura


class QRTests(SimpleTestCase):
    def test_url_consulta_segun_ambiente(self):
        self.assertIn(
            "catalogo-vpfe-hab.dian.gov.co",
            representacion.url_consulta_dian("ABC", ambiente=2),
        )
        self.assertIn(
            "catalogo-vpfe.dian.gov.co",
            representacion.url_consulta_dian("ABC", ambiente=1),
        )

    def test_genera_qr_png(self):
        png = representacion.generar_qr_png("https://demo")
        self.assertTrue(png.startswith(b"\x89PNG\r\n"))
        self.assertGreater(len(png), 100)


class PDFTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.documento.cufe_cude = "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33381030bc"
        cls.documento.save(update_fields=["cufe_cude"])

    def test_genera_pdf_valido(self):
        pdf = representacion.generar_pdf(self.documento, ambiente=2)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", pdf[-1024:])
        self.assertGreater(len(pdf), 2000)

    def test_pdf_contiene_datos_clave(self):
        pdf = representacion.generar_pdf(self.documento, ambiente=2)
        texto = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("323200000129", texto)   # número de factura
        self.assertIn("700085371", texto)      # NIT emisor
        self.assertIn("Producto demo", texto)  # descripción de la línea
        self.assertIn(self.documento.cufe_cude, texto)  # CUFE
