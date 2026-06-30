"""Pruebas de la API REST de documentos (flujo end-to-end)."""
import tempfile

from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption, pkcs12,
)
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Certificado

MEDIA_TEMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TEMP)
class DocumentoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.emisor = datos["emisor"]
        cls.cat = datos["catalogos"]

        # Adjuntar un certificado .p12 real al emisor.
        llave, cert = _generar_certificado()
        p12 = pkcs12.serialize_key_and_certificates(
            b"alias", llave, cert, None, BestAvailableEncryption(b"clave123")
        )
        certificado = Certificado(emisor=cls.emisor, clave="clave123", alias="test")
        certificado.archivo.save("test.p12", ContentFile(p12), save=True)

        cls.usuario = get_user_model().objects.create_user(
            email="tester@example.com", password="x"
        )

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _url(self, sufijo=""):
        return f"/api/documentos/documento/{self.documento.id}/{sufijo}"

    def test_listar_documentos(self):
        resp = self.client.get("/api/documentos/documento/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["count"], 1)
        # El listado NO incluye las líneas; el detalle (retrieve) sí.
        self.assertNotIn("detalles", resp.data["results"][0])
        detalle = self.client.get(f"/api/documentos/documento/{self.documento.id}/")
        self.assertIn("detalles", detalle.data)

    def test_filtrar_por_emisor_y_estado(self):
        url = "/api/documentos/documento/"
        # Emisor existente -> al menos 1; estado inexistente -> 0.
        self.assertGreaterEqual(
            self.client.get(url, {"emisor": self.emisor.id}).data["count"], 1
        )
        self.assertEqual(
            self.client.get(url, {"estado": "aceptado"}).data["count"], 0
        )
        self.assertEqual(
            self.client.get(url, {"emisor": 999999}).data["count"], 0
        )

    def test_emitir_firma_el_documento(self):
        resp = self.client.post(self._url("emitir/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.FIRMADO)
        self.assertEqual(len(resp.data["cufe_cude"]), 96)

    def test_descargar_xml_tras_emitir(self):
        self.client.post(self._url("emitir/"))
        resp = self.client.get(self._url("xml/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/xml")
        # FileResponse (stream) desde object storage.
        self.assertIn(b"<ds:Signature", b"".join(resp.streaming_content))

    def test_descargar_pdf_tras_emitir(self):
        self.client.post(self._url("emitir/"))
        resp = self.client.get(self._url("pdf/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_pdf_antes_de_emitir_falla(self):
        resp = self.client.get(self._url("pdf/"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_crear_documento_calcula_totales(self):
        c = self.cat
        payload = {
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.FACTURA_VENTA
            ).id,
            "emisor": self.emisor.id,
            "resolucion": self.documento.resolucion.id,
            "adquiriente": self.documento.adquiriente.id,
            "prefijo": "SETP",
            "consecutivo": 990000130,
            "numero": "SETP990000130",
            "fecha_emision": "2024-01-10",
            "hora_emision": "10:00:00",
            "moneda": c["cop"].id,
            "detalles": [
                {
                    "numero_linea": 1, "descripcion": "Servicio",
                    "cantidad": "2", "unidad_medida": c["unidad"].id,
                    "valor_unitario": "1000", "valor_total": "2000.00",
                    "impuestos": [
                        {"tributo": c["iva"].id, "base_gravable": "2000.00",
                         "tarifa": "19.00", "valor": "380.00"}
                    ],
                }
            ],
        }
        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["valor_bruto"], "2000.00")
        self.assertEqual(resp.data["total_impuestos"], "380.00")
        self.assertEqual(resp.data["total_a_pagar"], "2380.00")


class CatalogoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.catalogos.models import Tributo

        Tributo.objects.create(codigo="01", nombre="IVA")
        Tributo.objects.create(codigo="04", nombre="INC")

    def test_catalogo_es_publico_y_busca(self):
        resp = self.client.get("/api/catalogos/tributo/?search=IVA")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        codigos = [r["codigo"] for r in resp.data["results"]]
        self.assertIn("01", codigos)
        self.assertNotIn("04", codigos)
