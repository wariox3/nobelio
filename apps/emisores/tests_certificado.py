"""Pruebas de la API de certificado digital (.p12; archivo y clave write-only)."""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import CertificadoDigital, Emisor

_TMP_MEDIA = tempfile.mkdtemp()


def _crear_emisor(cat, nit="901192048"):
    return Emisor.objects.create(
        cuenta=cat["cuenta"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )


def _p12(nombre="cert.p12"):
    return SimpleUploadedFile(nombre, b"fake-p12-bytes", content_type="application/x-pkcs12")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class CertificadoDigitalAPITests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.client.force_authenticate(self.usuario)
        self.url = "/api/emisores/certificado/"

    def test_sube_certificado_sin_exponer_archivo_ni_clave(self):
        resp = self.client.post(
            self.url,
            {"emisor": self.emisor.id, "alias": "prod", "clave": "secreta",
             "archivo": _p12()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertNotIn("clave", resp.data)
        self.assertNotIn("archivo", resp.data)
        # Django puede añadir un sufijo si el nombre ya existe en el storage.
        self.assertTrue(resp.data["nombre_archivo"].startswith("cert"))
        self.assertTrue(resp.data["nombre_archivo"].endswith(".p12"))
        cert = CertificadoDigital.objects.get(pk=resp.data["id"])
        self.assertEqual(cert.clave, "secreta")  # sí se guardó

    def test_clave_y_archivo_obligatorios(self):
        resp = self.client.post(self.url, {"emisor": self.emisor.id}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("clave", resp.data)
        self.assertIn("archivo", resp.data)

    def test_filtra_por_emisor(self):
        CertificadoDigital.objects.create(emisor=self.emisor, clave="x", archivo=_p12())
        otro = _crear_emisor(self.cat, nit="800197268")
        CertificadoDigital.objects.create(emisor=otro, clave="y", archivo=_p12())
        resp = self.client.get(self.url, {"emisor": self.emisor.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    def test_requiere_autenticacion(self):
        resp = APIClient().post(
            self.url,
            {"emisor": self.emisor.id, "clave": "x", "archivo": _p12()},
            format="multipart",
        )
        self.assertIn(resp.status_code, (401, 403))
