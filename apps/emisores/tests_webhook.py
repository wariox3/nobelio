"""Los webhooks del emisor: el modelo y su API."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Webhook
from apps.emisores.models.webhook import MENSAJE_SOLO_HTTPS
from apps.emisores.serializers.webhook import MENSAJE_EMISOR_INMUTABLE
from apps.emisores.tests_software import _crear_emisor
from apps.nucleo.tests_utils import codigos, errores_por_campo

URL = "/api/emisores/webhook/"


class WebhookModeloTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emisor = crear_documento_factura()["documento"].emisor

    def test_acepta_una_url_https(self):
        Webhook(emisor=self.emisor, nombre="ERP", url="https://erp.co/hook").full_clean()

    def test_rechaza_lo_que_no_sea_https(self):
        for url in ("http://erp.co/hook", "ftp://erp.co/hook"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError) as caso:
                    Webhook(emisor=self.emisor, nombre="ERP", url=url).full_clean()
                self.assertIn(MENSAJE_SOLO_HTTPS, caso.exception.message_dict["url"])


class WebhookAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.emisor = datos["documento"].emisor
        cls.ajeno = _crear_emisor(datos["catalogos"], nit="800199436")
        cls.usuario = get_user_model().objects.create_user(
            email="webhooks@nobelio.co", password="x",
        )
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _crear(self, **extra):
        datos = {
            "emisor": self.emisor.id, "nombre": "ERP principal",
            "url": "https://erp.cliente.co/rededoc", "estado_validado": True,
            **extra,
        }
        return self.client.post(URL, datos, format="json")

    def test_crea_y_devuelve_el_webhook(self):
        resp = self._crear()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["nombre"], "ERP principal")
        self.assertTrue(resp.data["estado_validado"])
        # Sin mandarla, la bandera arranca apagada.
        self.assertFalse(resp.data["estado_notificado"])
        self.assertIn("creado_en", resp.data)

    def test_un_emisor_puede_tener_varios(self):
        for nombre in ("ERP", "Contabilidad", "Auditoría"):
            with self.subTest(nombre=nombre):
                resp = self._crear(nombre=nombre)
                self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Webhook.objects.filter(emisor=self.emisor).count(), 3)

    def test_solo_https_con_un_solo_error(self):
        """Una URL mal formada y una http reciben el mismo mensaje, una vez."""
        for url in ("http://erp.cliente.co/hook", "no es una url"):
            with self.subTest(url=url):
                resp = self._crear(url=url)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(errores_por_campo(resp), {"url": [MENSAJE_SOLO_HTTPS]})

    def test_se_actualiza(self):
        creado = self._crear()
        resp = self.client.patch(
            f"{URL}{creado.data['id']}/",
            {"url": "https://nuevo.co/hook", "estado_notificado": True},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        webhook = Webhook.objects.get()
        self.assertEqual(webhook.url, "https://nuevo.co/hook")
        self.assertTrue(webhook.estado_notificado)

    def test_no_cambia_de_emisor(self):
        """Ni siquiera a otro emisor propio: se borra y se crea en el otro."""
        self.usuario.emisores.add(self.ajeno)
        creado = self._crear()

        resp = self.client.patch(
            f"{URL}{creado.data['id']}/", {"emisor": self.ajeno.id}, format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {"emisor": [MENSAJE_EMISOR_INMUTABLE]})
        self.assertEqual(Webhook.objects.get().emisor, self.emisor)

    def test_un_emisor_ajeno_responde_como_inexistente(self):
        con_ajeno = self._crear(emisor=self.ajeno.id)
        con_inexistente = self._crear(emisor=999999)

        self.assertEqual(con_ajeno.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(codigos(con_ajeno), codigos(con_inexistente))

    def test_no_lista_ni_muestra_los_de_otra_cuenta(self):
        ajeno = Webhook.objects.create(
            emisor=self.ajeno, nombre="Ajeno", url="https://ajeno.co/hook",
        )
        propio = self._crear()

        listado = self.client.get(URL)
        self.assertEqual([w["id"] for w in listado.data["results"]], [propio.data["id"]])
        self.assertEqual(
            self.client.get(f"{URL}{ajeno.id}/").status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_filtra_por_emisor(self):
        self._crear()
        self.assertEqual(self.client.get(URL, {"emisor": self.emisor.id}).data["count"], 1)
        self.assertEqual(self.client.get(URL, {"emisor": self.ajeno.id}).data["count"], 0)

    def test_se_borra(self):
        creado = self._crear()
        resp = self.client.delete(f"{URL}{creado.data['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Webhook.objects.exists())
