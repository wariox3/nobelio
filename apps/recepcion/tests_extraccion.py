"""Extracción de documentos de un correo: adjuntos, ZIP y lectura del XML."""
from datetime import date, time
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from unittest import mock

from django.test import SimpleTestCase

from apps.recepcion import extraccion
from apps.recepcion.tests_utils import (
    CUFE, PDF, correo_con, xml_attached, xml_documento, zip_con,
)


def documentos(mime):
    return extraccion.documentos_del_correo(
        BytesParser(policy=policy.default).parsebytes(mime)
    )


class ExtraccionTests(SimpleTestCase):
    def test_attached_document_en_zip_con_su_pdf(self):
        mime = correo_con(("ad_FE-100.zip", zip_con(
            ad_FE__100__xml=xml_attached(), ad_FE__100__pdf=PDF,
        ), "application/zip"))

        [doc] = documentos(mime)

        self.assertEqual(doc.tipo, "factura_venta")
        self.assertEqual(doc.tipo_codigo_dian, "01")
        self.assertEqual(doc.numero, "FE-100")
        self.assertEqual(doc.cufe_cude, CUFE)
        self.assertEqual(doc.fecha_emision, date(2026, 10, 1))
        self.assertEqual(doc.hora_emision, time(10, 20, 30))
        self.assertEqual(doc.moneda, "COP")
        self.assertEqual(doc.proveedor_numero_identificacion, "800123456")
        self.assertEqual(doc.proveedor_digito_verificacion, "7")
        self.assertEqual(doc.proveedor_razon_social, "Proveedor Ejemplo S.A.S.")
        self.assertEqual(doc.proveedor_tipo_identificacion, "31")
        self.assertEqual(doc.proveedor_tipo_organizacion, "1")
        self.assertEqual(doc.receptor_numero_identificacion, "901192048")
        self.assertEqual(doc.valor_bruto, Decimal("100000.00"))
        self.assertEqual(doc.total_impuestos, Decimal("19800.00"))
        self.assertEqual(doc.total_a_pagar, Decimal("119800.00"))
        self.assertEqual(doc.validacion_codigo, "02")
        self.assertEqual(doc.fecha_validacion.date(), date(2026, 10, 1))
        self.assertEqual(doc.xml.nombre, "ad_FE.100.xml")
        self.assertTrue(doc.xml_documento.startswith(b"<?xml"))
        self.assertEqual(doc.pdf.contenido, PDF)

    def test_factura_suelta_adjunta_al_correo(self):
        [doc] = documentos(correo_con(("FE-100.xml", xml_documento(), "application/xml")))

        self.assertEqual(doc.numero, "FE-100")
        self.assertEqual(doc.xml_documento, b"")
        self.assertIsNone(doc.pdf)

    def test_proveedor_persona_natural(self):
        xml = xml_documento(tipo_proveedor="13", organizacion_proveedor="2")
        [doc] = documentos(correo_con(("FE-100.xml", xml, "application/xml")))

        self.assertEqual(doc.proveedor_tipo_identificacion, "13")
        self.assertEqual(doc.proveedor_tipo_organizacion, "2")

    def test_notas_credito_y_debito(self):
        mime = correo_con(
            ("nc.xml", xml_documento("CreditNote", cufe="c" * 96, tipo="91"), "text/xml"),
            ("nd.xml", xml_documento(
                "DebitNote", cufe="d" * 96, tipo="92", totales="RequestedMonetaryTotal",
            ), "text/xml"),
        )

        nc, nd = documentos(mime)

        self.assertEqual((nc.tipo, nc.tipo_codigo_dian), ("nota_credito", "91"))
        self.assertEqual((nd.tipo, nd.tipo_codigo_dian), ("nota_debito", "92"))
        self.assertEqual(nd.total_a_pagar, Decimal("119800.00"))

    def test_documento_soporte_no_es_una_compra(self):
        mime = correo_con(("ds.xml", xml_documento(tipo="05"), "text/xml"))

        self.assertEqual(documentos(mime), [])

    def test_zip_dentro_de_zip(self):
        interno = zip_con(fe__xml=xml_attached())
        mime = correo_con(("externo.zip", zip_con(interno__zip=interno), "application/zip"))

        self.assertEqual(len(documentos(mime)), 1)

    def test_correo_adjunto(self):
        reenviado = BytesParser(policy=policy.default).parsebytes(
            correo_con(("fe.zip", zip_con(fe__xml=xml_attached()), "application/zip"))
        )
        mime = correo_con(("reenviado.eml", reenviado, "message/rfc822"))

        self.assertEqual(len(documentos(mime)), 1)

    def test_eml_como_archivo(self):
        eml = correo_con(("fe.xml", xml_attached(), "application/xml"))
        mime = correo_con(("original.eml", eml, "application/octet-stream"))

        self.assertEqual(len(documentos(mime)), 1)

    def test_pdf_en_el_correo_y_xml_en_el_zip(self):
        mime = correo_con(
            ("fe.zip", zip_con(fe__xml=xml_attached()), "application/zip"),
            ("representacion.pdf", PDF, "application/pdf"),
        )

        [doc] = documentos(mime)

        self.assertEqual(doc.pdf.contenido, PDF)

    def test_dos_documentos_emparejan_el_pdf_por_nombre(self):
        mime = correo_con(("lote.zip", zip_con(
            a__xml=xml_attached(xml_documento(numero="A1", cufe="1" * 96)),
            b__xml=xml_attached(xml_documento(numero="B2", cufe="2" * 96)),
            b__pdf=PDF,
        ), "application/zip"))

        a, b = documentos(mime)

        self.assertIsNone(a.pdf)
        self.assertEqual(b.pdf.contenido, PDF)

    def test_un_xml_que_no_es_documento_se_ignora(self):
        mime = correo_con(
            ("otro.xml", b"<?xml version='1.0'?><cualquier/>", "text/xml"),
            ("roto.xml", b"<no-cierra>", "text/xml"),
            ("roto.zip", b"no es un zip", "application/zip"),
        )

        self.assertEqual(documentos(mime), [])

    def test_un_docx_no_se_abre_como_zip(self):
        mime = correo_con(("contrato.docx", zip_con(word__document__xml=xml_documento()),
                           "application/octet-stream"))

        self.assertEqual(documentos(mime), [])

    def test_no_resuelve_entidades_externas(self):
        xxe = xml_documento(numero="&xxe;").replace(
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE Invoice [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>',
        )

        docs = documentos(correo_con(("xxe.xml", xxe, "text/xml")))

        # Sin la entidad resuelta no hay número: no es un documento válido.
        self.assertEqual(docs, [])

    def test_contenido_que_pasa_el_tope(self):
        mime = correo_con(("fe.zip", zip_con(fe__xml=xml_attached()), "application/zip"))

        with mock.patch.object(extraccion, "MAXIMOS_BYTES", 1000):
            with self.assertRaises(extraccion.ContenidoExcesivo):
                documentos(mime)

    def test_demasiados_zip_anidados(self):
        contenido = zip_con(fe__xml=xml_attached())
        for _ in range(extraccion.MAXIMA_PROFUNDIDAD + 1):
            contenido = zip_con(capa__zip=contenido)

        with self.assertRaises(extraccion.ContenidoExcesivo):
            documentos(correo_con(("capas.zip", contenido, "application/zip")))
