"""Pruebas de la API: `parametros_webhook` en la creación y la lectura del documento.

Es un objeto libre del ERP que vuelve en los avisos de webhook; los avisos se
prueban en ``apps/emisores/tests_webhook_envio.py``.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos import tests_tipo_operacion as base
from apps.documentos.models import Documento
from apps.documentos.serializers.documento import (
    MAXIMO_PARAMETROS_WEBHOOK,
    MENSAJE_PARAMETROS_WEBHOOK_GRANDE,
    MENSAJE_PARAMETROS_WEBHOOK_NO_OBJETO,
)
from apps.nucleo.tests_utils import codigos, errores_por_campo

URL = base.URL


class ParametrosWebhookAPITests(APITestCase):
    # El usuario, el emisor y el payload de una factura que se crea son los de
    # las pruebas del tipo de operación; se toman del módulo para no correr
    # también sus pruebas aquí.
    setUpTestData = classmethod(base.TipoOperacionAPITests.setUpTestData.__func__)
    setUp = base.TipoOperacionAPITests.setUp
    _linea = base.TipoOperacionAPITests._linea
    _payload = base.TipoOperacionAPITests._payload
    _crear = base.TipoOperacionAPITests._crear

    def assertRechazado(self, resp, mensaje):
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("detail", resp.data)
        self.assertIn(mensaje, errores_por_campo(resp).get("parametros_webhook", []), resp.data)

    def test_sin_el_campo_crea_como_siempre(self):
        resp = self._crear()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data["parametros_webhook"])
        self.assertIsNone(Documento.objects.get(pk=resp.data["id"]).parametros_webhook)

    def test_admite_null(self):
        resp = self._crear(parametros_webhook=None)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(Documento.objects.get(pk=resp.data["id"]).parametros_webhook)

    def test_se_guarda_y_se_lee_tal_cual(self):
        # Las claves de dentro son libres: no pasan por la estructura estricta.
        parametros = {"modulo": "TUR", "anidado": {"x": [1, "dos", None]}, "n": 1.5}

        resp = self._crear(parametros_webhook=parametros)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["parametros_webhook"], parametros)
        documento = Documento.objects.get(pk=resp.data["id"])
        self.assertEqual(documento.parametros_webhook, parametros)
        lectura = self.client.get(f"{URL}{documento.pk}/")
        self.assertEqual(lectura.data["parametros_webhook"], parametros)

    def test_si_no_es_objeto_es_400(self):
        for valor in ("TUR", ["TUR"], 5, True):
            with self.subTest(valor=valor):
                resp = self._crear(parametros_webhook=valor)

                self.assertRechazado(resp, MENSAJE_PARAMETROS_WEBHOOK_NO_OBJETO)
                self.assertNotIn("campo_desconocido", codigos(resp))

    def test_pasar_del_tope_es_400(self):
        # `{"m":"…"}` compacto son 8 bytes más el texto.
        justo = {"m": "x" * (MAXIMO_PARAMETROS_WEBHOOK - 8)}
        grande = {"m": "x" * (MAXIMO_PARAMETROS_WEBHOOK - 7)}

        self.assertEqual(self._crear(parametros_webhook=justo).status_code, status.HTTP_201_CREATED)
        self.assertRechazado(
            self._crear(parametros_webhook=grande), MENSAJE_PARAMETROS_WEBHOOK_GRANDE,
        )

    def test_el_tope_se_mide_en_bytes(self):
        # «ñ» ocupa dos bytes en UTF-8.
        grande = {"m": "ñ" * (MAXIMO_PARAMETROS_WEBHOOK // 2)}

        self.assertRechazado(
            self._crear(parametros_webhook=grande), MENSAJE_PARAMETROS_WEBHOOK_GRANDE,
        )
