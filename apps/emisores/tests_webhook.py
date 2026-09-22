"""Los webhooks del emisor: el modelo y su API."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Webhook
from apps.emisores.models.webhook import MENSAJE_URL_WEB
from apps.emisores.serializers.webhook import MENSAJE_EMISOR_INMUTABLE
from apps.emisores.tests_software import _crear_emisor
from apps.nucleo.tests_utils import codigos, errores_por_campo

URL = "/api/emisores/webhook/"


class WebhookModeloTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emisor = crear_documento_factura()["documento"].emisor

    def test_acepta_http_y_https(self):
        for url in ("https://erp.co/hook", "http://erp.co/hook"):
            with self.subTest(url=url):
                Webhook(emisor=self.emisor, nombre="ERP", url=url).full_clean()

    def test_rechaza_lo_que_no_sea_http_ni_https(self):
        with self.assertRaises(ValidationError) as caso:
            Webhook(emisor=self.emisor, nombre="ERP", url="ftp://erp.co/hook").full_clean()
        self.assertIn(MENSAJE_URL_WEB, caso.exception.message_dict["url"])


class WebhookSecretoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emisor = crear_documento_factura()["documento"].emisor

    def _en_la_base(self, webhook):
        with connection.cursor() as cursor:
            cursor.execute("SELECT secreto FROM emi_webhook WHERE id = %s", [webhook.id])
            return cursor.fetchone()[0]

    def test_se_guarda_cifrado_y_se_lee_en_claro(self):
        webhook = Webhook.objects.create(
            emisor=self.emisor, nombre="ERP", url="https://erp.co/hook", secreto="s3creto",
        )

        self.assertNotIn("s3creto", self._en_la_base(webhook))
        self.assertEqual(Webhook.objects.get(pk=webhook.pk).secreto, "s3creto")

    def test_se_cifra_con_su_propia_clave(self):
        # Con la de los certificados en su lugar, el token no abre: no se
        # comparten clave aunque usen el mismo campo.
        webhook = Webhook.objects.create(
            emisor=self.emisor, nombre="ERP", url="https://erp.co/hook", secreto="s3creto",
        )
        from apps.utilidades.cifrado import descifrar

        token = self._en_la_base(webhook)
        self.assertEqual(descifrar(token), token)
        self.assertEqual(descifrar(token, "WEBHOOK_ENCRYPTION_KEY"), "s3creto")

    def test_vacio_no_se_cifra(self):
        webhook = Webhook.objects.create(emisor=self.emisor, nombre="ERP", url="https://erp.co/hook")

        self.assertEqual(self._en_la_base(webhook), "")

    @override_settings(WEBHOOK_ENCRYPTION_KEY="")
    def test_sin_clave_no_se_guarda_un_secreto(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "WEBHOOK_ENCRYPTION_KEY"):
            Webhook.objects.create(
                emisor=self.emisor, nombre="ERP", url="https://erp.co/hook", secreto="s3creto",
            )


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

    def test_el_secreto_es_opcional(self):
        resp = self._crear()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Webhook.objects.get(pk=resp.data["id"]).secreto, "")

    def test_el_secreto_se_guarda_y_no_sale_en_las_respuestas(self):
        resp = self._crear(secreto="s3creto")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertNotIn("secreto", resp.data)
        self.assertEqual(Webhook.objects.get(pk=resp.data["id"]).secreto, "s3creto")
        self.assertNotIn("secreto", self.client.get(f"{URL}{resp.data['id']}/").data)
        self.assertNotIn("secreto", self.client.get(URL).data["results"][0])

    def test_el_secreto_se_cambia_y_se_quita(self):
        pk = self._crear(secreto="viejo").data["id"]

        self.client.patch(f"{URL}{pk}/", {"secreto": "nuevo"}, format="json")
        self.assertEqual(Webhook.objects.get(pk=pk).secreto, "nuevo")

        self.client.patch(f"{URL}{pk}/", {"secreto": ""}, format="json")
        self.assertEqual(Webhook.objects.get(pk=pk).secreto, "")

    def test_un_emisor_puede_tener_varios(self):
        for nombre in ("ERP", "Contabilidad", "Auditoría"):
            with self.subTest(nombre=nombre):
                resp = self._crear(nombre=nombre)
                self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Webhook.objects.filter(emisor=self.emisor).count(), 3)

    def test_acepta_http(self):
        resp = self._crear(url="http://erp.cliente.co/hook")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_url_invalida_con_un_solo_error(self):
        """Una URL mal formada y una ftp reciben el mismo mensaje, una vez."""
        for url in ("ftp://erp.cliente.co/hook", "no es una url"):
            with self.subTest(url=url):
                resp = self._crear(url=url)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(errores_por_campo(resp), {"url": [MENSAJE_URL_WEB]})

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
