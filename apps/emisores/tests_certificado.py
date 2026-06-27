"""Pruebas de la API de certificado digital (.p12; archivo y clave write-only)."""
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Certificado, Emisor

_TMP_MEDIA = tempfile.mkdtemp()

# Generar llaves RSA es costoso: las reutilizamos en todas las pruebas.
_LLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_LLAVE_DEBIL = rsa.generate_private_key(public_exponent=65537, key_size=1024)


def _crear_emisor(cat, nit="901192048"):
    return Emisor.objects.create(
        cuenta=cat["cuenta"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )


def _bytes_p12(nit="901192048", clave=b"secreta", llave=_LLAVE, desde=None, dias=365):
    """Construye un .p12 real con el NIT en el serialNumber del subject."""
    desde = desde or datetime.now(timezone.utc) - timedelta(days=1)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "SEMANTICA DIGITAL SAS"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, nit),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(desde).not_valid_after(desde + timedelta(days=dias))
        .sign(llave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"cert", llave, cert, None, BestAvailableEncryption(clave)
    )


def _p12(nombre="cert.p12", **kwargs):
    return SimpleUploadedFile(
        nombre, _bytes_p12(**kwargs), content_type="application/x-pkcs12"
    )


# B2 habilitado: el certificado solo puede guardarse en Backblaze (nunca en
# disco local). En pruebas el FileField ya quedó ligado al storage local —
# capturado al importar el modelo— así que escribe en MEDIA_ROOT temporal sin
# tocar B2; aquí solo simulamos que B2 está configurado para superar la guarda.
@override_settings(MEDIA_ROOT=_TMP_MEDIA, B2_HABILITADO=True)
class CertificadoAPITests(APITestCase):
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
        self.url_cargar = "/api/emisores/certificado/cargar/"

    def test_sube_certificado_sin_exponer_archivo_ni_clave(self):
        resp = self.client.post(
            self.url_cargar,
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
        # La vigencia se autocompleta desde el propio certificado.
        self.assertIsNotNone(resp.data["vigente_desde"])
        self.assertIsNotNone(resp.data["vigente_hasta"])
        cert = Certificado.objects.get(pk=resp.data["id"])
        self.assertEqual(cert.clave, "secreta")  # sí se guardó
        self.assertIsNotNone(cert.vigente_hasta)

    def _cargar(self, **extra):
        datos = {"emisor": self.emisor.id, "clave": "secreta", "archivo": _p12()}
        datos.update(extra)
        return self.client.post(self.url_cargar, datos, format="multipart")

    def test_rechaza_clave_incorrecta(self):
        resp = self.client.post(
            self.url_cargar,
            {"emisor": self.emisor.id, "clave": "incorrecta", "archivo": _p12()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Certificado.objects.count(), 0)

    def test_rechaza_archivo_que_no_es_pkcs12(self):
        archivo = SimpleUploadedFile(
            "cert.p12", b"no-soy-un-p12", content_type="application/x-pkcs12"
        )
        resp = self._cargar(archivo=archivo)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Certificado.objects.count(), 0)

    def test_rechaza_certificado_vencido(self):
        vencido = _p12(desde=datetime.now(timezone.utc) - timedelta(days=400), dias=30)
        resp = self._cargar(archivo=vencido)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("vencido", resp.data["detail"])
        self.assertEqual(Certificado.objects.count(), 0)

    def test_rechaza_llave_rsa_debil(self):
        debil = _p12(llave=_LLAVE_DEBIL)
        resp = self._cargar(archivo=debil)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Certificado.objects.count(), 0)

    def test_rechaza_nit_que_no_corresponde_al_emisor(self):
        ajeno = _p12(nit="800197268")  # NIT distinto al del emisor
        resp = self._cargar(archivo=ajeno)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no corresponde al emisor", resp.data["detail"])
        self.assertEqual(Certificado.objects.count(), 0)

    def test_cargar_desactiva_certificados_previos_del_emisor(self):
        previo = Certificado.objects.create(
            emisor=self.emisor, clave="x", archivo=_p12(), activo=True
        )
        otro_emisor = _crear_emisor(self.cat, nit="800197268")
        ajeno = Certificado.objects.create(
            emisor=otro_emisor, clave="y", archivo=_p12(), activo=True
        )
        resp = self.client.post(
            self.url_cargar,
            {"emisor": self.emisor.id, "clave": "secreta", "archivo": _p12()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        previo.refresh_from_db()
        ajeno.refresh_from_db()
        nuevo = Certificado.objects.get(pk=resp.data["id"])
        self.assertFalse(previo.activo)   # el anterior del emisor se desactivó
        self.assertTrue(nuevo.activo)     # el recién cargado queda vigente
        self.assertTrue(ajeno.activo)     # el de otro emisor no se toca

    def test_clave_y_archivo_obligatorios(self):
        resp = self.client.post(
            self.url_cargar, {"emisor": self.emisor.id}, format="multipart"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("clave", resp.data)
        self.assertIn("archivo", resp.data)

    def test_post_generico_deshabilitado(self):
        # La creación genérica está bloqueada: solo se sube por 'cargar'.
        resp = self.client.post(
            self.url,
            {"emisor": self.emisor.id, "clave": "secreta", "archivo": _p12()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 405)

    def test_filtra_por_emisor(self):
        Certificado.objects.create(emisor=self.emisor, clave="x", archivo=_p12())
        otro = _crear_emisor(self.cat, nit="800197268")
        Certificado.objects.create(emisor=otro, clave="y", archivo=_p12())
        resp = self.client.get(self.url, {"emisor": self.emisor.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    @override_settings(B2_HABILITADO=False)
    def test_rechaza_si_backblaze_no_configurado(self):
        # Sin B2 configurado no debe poder guardarse el .p12 (jamás en disco).
        resp = self.client.post(
            self.url_cargar,
            {"emisor": self.emisor.id, "clave": "secreta", "archivo": _p12()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Certificado.objects.count(), 0)

    def test_requiere_autenticacion(self):
        resp = APIClient().post(
            self.url_cargar,
            {"emisor": self.emisor.id, "clave": "x", "archivo": _p12()},
            format="multipart",
        )
        self.assertIn(resp.status_code, (401, 403))
