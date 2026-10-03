"""El endpoint que registra los correos del Email Worker."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.recepcion.models import Correo

URL = "/recepcion/inbound"
TOKEN = "token-de-prueba"
MIME = (
    b"From: Proveedor <facturas@proveedor.example>\r\n"
    b"To: compras@cliente.example\r\n"
    b"Subject: Factura FE-100\r\n"
    b"\r\n"
    b"Adjunto la factura.\r\n"
)
CABECERAS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Envelope-To": "VeloEnvios@recepcion.rededoc.co",
    "X-Envelope-From": "compras@cliente.example",
    "X-Raw-Key": "2026-10-02/0b7c1d2e.eml",
}


@override_settings(INBOUND_TOKEN=TOKEN)
class InboundTests(TestCase):
    def publicar(self, cuerpo=MIME, cabeceras=CABECERAS):
        return self.client.post(
            URL, data=cuerpo, content_type="message/rfc822", headers=cabeceras,
        )

    def test_registra_el_correo_pendiente(self):
        respuesta = self.publicar()

        self.assertEqual(respuesta.status_code, 201)
        correo = Correo.objects.get()
        self.assertEqual(respuesta.json(), {"id": correo.pk, "estado": "pendiente"})
        self.assertEqual(correo.alias, "veloenvios")
        self.assertEqual(correo.envelope_to, "VeloEnvios@recepcion.rededoc.co")
        self.assertEqual(correo.envelope_from, "compras@cliente.example")
        self.assertEqual(correo.raw_key, "2026-10-02/0b7c1d2e.eml")
        self.assertEqual(len(correo.sha256), 64)
        self.assertIsNone(correo.emisor)

    def test_el_mismo_correo_no_se_registra_dos_veces(self):
        primero = self.publicar()
        segundo = self.publicar()

        self.assertEqual(segundo.status_code, 200)
        self.assertEqual(segundo.json()["id"], primero.json()["id"])
        self.assertEqual(Correo.objects.count(), 1)

    def test_otro_correo_es_otro_registro(self):
        self.publicar()
        self.publicar(cuerpo=MIME + b"otro")

        self.assertEqual(Correo.objects.count(), 2)

    def test_sin_destinatario_es_400(self):
        cabeceras = {k: v for k, v in CABECERAS.items() if k != "X-Envelope-To"}

        respuesta = self.publicar(cabeceras=cabeceras)

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Correo.objects.exists())

    def test_mas_grande_que_el_tope_es_413(self):
        with mock.patch("apps.recepcion.views.inbound.MAXIMO_BYTES", len(MIME) - 1):
            respuesta = self.publicar()

        self.assertEqual(respuesta.status_code, 413)
        self.assertFalse(Correo.objects.exists())

    def test_mas_grande_que_el_tope_de_django_se_acepta(self):
        # 3 MB: por encima de DATA_UPLOAD_MAX_MEMORY_SIZE (2.5 MB).
        respuesta = self.publicar(cuerpo=MIME + b"x" * (3 * 1024 * 1024))

        self.assertEqual(respuesta.status_code, 201)

    def test_sin_token_es_401(self):
        cabeceras = {k: v for k, v in CABECERAS.items() if k != "Authorization"}

        respuesta = self.publicar(cabeceras=cabeceras)

        self.assertEqual(respuesta.status_code, 401)
        self.assertFalse(Correo.objects.exists())

    def test_token_equivocado_es_401(self):
        respuesta = self.publicar(cabeceras={**CABECERAS, "Authorization": "Bearer otro"})

        self.assertEqual(respuesta.status_code, 401)
        self.assertFalse(Correo.objects.exists())

    def test_otro_esquema_es_401(self):
        respuesta = self.publicar(cabeceras={**CABECERAS, "Authorization": f"Basic {TOKEN}"})

        self.assertEqual(respuesta.status_code, 401)

    def test_el_esquema_no_distingue_mayusculas(self):
        respuesta = self.publicar(cabeceras={**CABECERAS, "Authorization": f"bearer {TOKEN}"})

        self.assertEqual(respuesta.status_code, 201)

    @override_settings(INBOUND_TOKEN="")
    def test_sin_token_configurado_rechaza_todo(self):
        respuesta = self.publicar(cabeceras={**CABECERAS, "Authorization": "Bearer "})

        self.assertEqual(respuesta.status_code, 401)
        self.assertFalse(Correo.objects.exists())

    def test_solo_acepta_post(self):
        self.assertEqual(self.client.get(URL).status_code, 405)
