"""`POST documento/{id}/respuesta-validado/`: avisar la validación y marcarla."""
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.models import DocumentoEstado
from apps.documentos.tests_utils import crear_documento_factura
from apps.documentos.views.documento import MENSAJE_RESPUESTA_YA_VALIDADO
from apps.emisores.models import Webhook, WebhookAviso

POST = "apps.emisores.servicios.webhooks.requests.post"


def _respuesta(codigo):
    respuesta = mock.Mock(status_code=codigo, text="")
    respuesta.json.side_effect = ValueError("sin cuerpo")
    return respuesta


class RespuestaValidadoTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.emisor = datos["emisor"]
        cls.emisor.referencia_externa = "12"
        cls.emisor.save(update_fields=["referencia_externa"])
        cls.documento.estado = DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.ACEPTADO)
        cls.documento.fecha_validacion = datetime.datetime(
            2026, 9, 21, 10, 15, tzinfo=ZoneInfo("America/Bogota"),
        )
        cls.documento.cufe_cude = "cufe-de-prueba"
        cls.documento.save()
        cls.webhook = Webhook.objects.create(
            emisor=cls.emisor, nombre="torio", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )
        cls.usuario = get_user_model().objects.create_user(email="respuesta@nobelio.co", password="x")
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _llamar(self):
        return self.client.post(f"/api/documentos/documento/{self.documento.pk}/respuesta-validado/")

    def _respondido(self):
        self.documento.refresh_from_db()
        return self.documento.respuesta_validado

    def test_con_un_200_queda_respondido(self):
        with mock.patch(POST, return_value=_respuesta(200)) as post:
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["respuesta_validado"])
        self.assertEqual(resp.data["avisos"][0]["codigo_http"], 200)
        self.assertTrue(self._respondido())
        post.assert_called_once()
        self.assertEqual(WebhookAviso.objects.get().tipo, WebhookAviso.Tipo.VALIDACION)

    def test_sin_200_no_se_marca_y_responde_502(self):
        # También el 409: el que da por respondido es solo el 200.
        for codigo in (409, 401, 503):
            with self.subTest(codigo=codigo):
                with mock.patch(POST, return_value=_respuesta(codigo)):
                    resp = self._llamar()

                self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY, resp.data)
                self.assertIn(str(codigo), resp.data["detail"])
                self.assertFalse(self._respondido())

    def test_torio_caido_no_se_marca(self):
        with mock.patch(POST, side_effect=requests.ConnectionError("caído")):
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY, resp.data)
        self.assertFalse(self._respondido())

    def test_basta_un_200_entre_varios_webhooks(self):
        Webhook.objects.create(
            emisor=self.emisor, nombre="otro", url="https://otro.co/hook",
            estado_validado=True, secreto="s3creto",
        )

        with mock.patch(POST, side_effect=[_respuesta(503), _respuesta(200)]):
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(len(resp.data["avisos"]), 2)
        self.assertTrue(self._respondido())

    def test_solo_documentos_aceptados(self):
        self.documento.estado = DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.ENVIADO)
        self.documento.save(update_fields=["estado"])

        with mock.patch(POST) as post:
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("enviado", resp.data["detail"])
        post.assert_not_called()

    def test_ya_respondido_no_se_vuelve_a_avisar(self):
        self.documento.respuesta_validado = True
        self.documento.save(update_fields=["respuesta_validado"])

        with mock.patch(POST) as post:
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(resp.data["detail"], MENSAJE_RESPUESTA_YA_VALIDADO)
        post.assert_not_called()

    def test_sin_webhook_de_validacion_se_marca_sin_enviar(self):
        Webhook.objects.filter(pk=self.webhook.pk).update(estado_validado=False, estado_notificado=True)

        with mock.patch(POST) as post:
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data, {"respuesta_validado": True, "avisos": []})
        self.assertTrue(self._respondido())
        post.assert_not_called()
        self.assertFalse(WebhookAviso.objects.exists())

    def test_documento_ajeno_es_404(self):
        otro = get_user_model().objects.create_user(email="ajeno@nobelio.co", password="x")
        self.client.force_authenticate(otro)

        with mock.patch(POST) as post:
            resp = self._llamar()

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        post.assert_not_called()
