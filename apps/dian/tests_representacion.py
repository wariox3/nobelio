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

    def test_el_titulo_del_pdf_es_el_del_tipo_de_documento(self):
        """Decía «Factura» siempre, también en una nota crédito o un P.O.S."""
        pdf = representacion.generar_pdf(self.documento, ambiente=2)
        metadatos = PdfReader(io.BytesIO(pdf)).metadata
        self.assertIn(self.documento.documento_tipo.nombre, metadatos.title)


class PDFPorTipoTests(TestCase):
    """Lo que el PDF decía de sí mismo y no era cierto fuera de la factura."""

    @classmethod
    def setUpTestData(cls):
        from datetime import date
        from decimal import Decimal

        from apps.documentos import models as doc
        from apps.documentos.tests_utils import crear_adquiriente

        cls.base = crear_documento_factura()
        c = cls.base["catalogos"]
        cls.ds = doc.Documento.objects.create(
            documento_tipo=doc.DocumentoTipo.objects.get(
                codigo=doc.DocumentoTipo.Codigo.DOCUMENTO_SOPORTE
            ),
            emisor=cls.base["emisor"], prefijo="DS", consecutivo=1, numero="DS1",
            fecha_emision=date(2026, 9, 1), hora_emision="10:00:00",
            moneda=c["cop"], valor_bruto=Decimal("100000.00"),
            total_impuestos=Decimal("0.00"), total_a_pagar=Decimal("100000.00"),
            cufe_cude="d" * 96,
        )
        crear_adquiriente(cls.ds, c)
        detalle = doc.DocumentoDetalle.objects.create(
            documento=cls.ds, numero_linea=1, descripcion="Servicio de un no obligado",
            cantidad=Decimal("1"), unidad_medida=c["unidad"],
            valor_unitario=Decimal("100000"), valor_total=Decimal("100000.00"),
        )
        from apps.catalogos.models import Tributo

        # `es_retencion` no es un campo sino una propiedad del código: el 06
        # (ReteFuente) ya lo es por serlo.
        retencion, _ = Tributo.objects.get_or_create(
            codigo="06", defaults={"nombre": "ReteFuente"},
        )
        doc.DocumentoDetalleImpuesto.objects.create(
            detalle=detalle, tributo=retencion,
            base_gravable=Decimal("100000.00"), tarifa=Decimal("4.00"),
            valor=Decimal("4000.00"),
        )

    def _texto(self, documento):
        pdf = representacion.generar_pdf(documento, ambiente=2)
        return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)

    def test_el_pie_nombra_el_tipo_y_no_la_factura(self):
        texto = self._texto(self.ds)
        self.assertIn("Representación gráfica de", texto)
        self.assertNotIn("factura electrónica de venta", texto)

    def test_el_identificador_del_documento_soporte_es_el_cuds(self):
        """No lleva CUFE ni CUDE: la etiqueta la manda el constructor UBL."""
        self.assertIn("CUDS", self._texto(self.ds))

    def test_las_retenciones_aparecen_en_los_totales(self):
        """No suman al total a pagar, pero el papel tiene que decir qué se retuvo."""
        texto = self._texto(self.ds)
        self.assertIn("Retenciones practicadas", texto)
        self.assertIn("Neto a girar", texto)

    def test_la_factura_no_muestra_retenciones(self):
        """Su tipo no las lleva, así que la fila no debe aparecer."""
        self.assertNotIn("Retenciones practicadas", self._texto(self.base["documento"]))
