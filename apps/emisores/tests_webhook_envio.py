"""El envío de los avisos de webhook: firma, cuerpo, respuestas y disparadores.

El contrato es `torio/docs/webhook_rededoc.md`; el vector de prueba sale de ahí.
"""
import datetime
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from apps.dian.servicios import registrar_cambio_de_estado
from apps.documentos.models import DocumentoEstado
from apps.documentos.servicios import marcar_notificado
from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Webhook, WebhookAviso
from apps.emisores.servicios import webhooks
from apps.emisores.tests_software import _crear_emisor

# El vector de prueba del contrato (sección 4), tal cual.
SECRETO_VECTOR = "secreto-de-ejemplo"
FECHA_VECTOR = "1790000000"
CUERPO_VECTOR = (
    '{"tipo": "validacion", "cliente": 12, "documento": '
    '"0b8f3c2e-5d7a-4e1b-9c6f-2a4d8e7b1f90", "fecha_validacion": '
    '"2026-09-21T10:15:00-05:00", "cufe": "abc123"}'
)
FIRMA_VECTOR = "v1=2baa83d05c60f6d0843109233e07935bfe0225aaa776c1c59dfcc6e1e2c0ba5e"

POST = "apps.emisores.servicios.webhooks.requests.post"


def _respuesta(codigo, detalle=None):
    respuesta = mock.Mock(status_code=codigo, text=detalle or "")
    respuesta.json.return_value = {"detail": detalle} if detalle else {}
    if not detalle:
        respuesta.json.side_effect = ValueError("sin cuerpo")
    return respuesta


class FirmaTests(TestCase):
    def test_el_vector_de_prueba_da_la_firma_esperada(self):
        firma = webhooks.firmar(SECRETO_VECTOR, FECHA_VECTOR, CUERPO_VECTOR.encode())

        self.assertEqual(firma, FIRMA_VECTOR)

    def test_el_cuerpo_sale_byte_a_byte_como_el_del_vector(self):
        # Si el JSON saliera con otros espacios u otro orden, la firma de torio
        # —que se calcula sobre los bytes recibidos— seguiría cuadrando, pero
        # este es el formato que el contrato documenta.
        documento = SimpleNamespace(
            pk=uuid.UUID("0b8f3c2e-5d7a-4e1b-9c6f-2a4d8e7b1f90"),
            emisor=SimpleNamespace(referencia_externa="12"),
            fecha_validacion=datetime.datetime(2026, 9, 21, 15, 15, tzinfo=datetime.timezone.utc),
            cufe_cude="abc123",
        )

        cuerpo = webhooks.armar_cuerpo(documento, WebhookAviso.Tipo.VALIDACION)

        self.assertEqual(cuerpo, CUERPO_VECTOR)

    def test_la_notificacion_no_lleva_fecha_ni_cufe(self):
        documento = SimpleNamespace(
            pk=uuid.uuid4(), emisor=SimpleNamespace(referencia_externa="7"),
        )

        cuerpo = json.loads(webhooks.armar_cuerpo(documento, WebhookAviso.Tipo.NOTIFICACION))

        self.assertEqual(set(cuerpo), {"tipo", "cliente", "documento"})
        self.assertEqual(cuerpo["cliente"], 7)


class EntregaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.webhook = Webhook.objects.create(
            emisor=datos["emisor"], nombre="torio", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )

    def _aviso(self):
        return WebhookAviso.objects.create(
            webhook=self.webhook, documento=self.documento,
            tipo=WebhookAviso.Tipo.NOTIFICACION, cuerpo='{"tipo": "notificacion"}',
        )

    def test_manda_los_bytes_firmados_con_la_hora_del_envio(self):
        aviso = self._aviso()

        with mock.patch(POST, return_value=_respuesta(200)) as post, \
                mock.patch("apps.emisores.servicios.webhooks.time.time", return_value=1790000000.7):
            webhooks.entregar(aviso)

        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"], b'{"tipo": "notificacion"}')
        self.assertNotIn("json", kwargs)
        cabeceras = kwargs["headers"]
        self.assertEqual(cabeceras["X-Rededoc-Fecha"], "1790000000")
        esperada = hmac.new(
            b"s3creto", b'1790000000.{"tipo": "notificacion"}', hashlib.sha256,
        ).hexdigest()
        self.assertEqual(cabeceras["X-Rededoc-Firma"], f"v1={esperada}")
        self.assertEqual(cabeceras["Content-Type"], "application/json")
        self.assertNotIn("X-Tenant", cabeceras)

    def test_cada_envio_se_firma_con_su_propia_hora(self):
        firmas = []
        for ahora in (1790000000, 1790000600):
            with mock.patch(POST, return_value=_respuesta(200)) as post, \
                    mock.patch("apps.emisores.servicios.webhooks.time.time", return_value=ahora):
                webhooks.entregar(self._aviso())
            firmas.append(post.call_args.kwargs["headers"]["X-Rededoc-Firma"])

        self.assertNotEqual(*firmas)

    def test_la_tabla_de_respuestas_del_contrato(self):
        casos = {
            200: WebhookAviso.Estado.ENTREGADO,
            409: WebhookAviso.Estado.ENTREGADO,  # ya estaba validado
            400: WebhookAviso.Estado.FALLIDO,
            401: WebhookAviso.Estado.FALLIDO,
            404: WebhookAviso.Estado.FALLIDO,
            429: WebhookAviso.Estado.FALLIDO,
            500: WebhookAviso.Estado.FALLIDO,
            503: WebhookAviso.Estado.FALLIDO,
        }
        for codigo, estado in casos.items():
            with self.subTest(codigo=codigo):
                detalle = None if codigo == 200 else f"detalle {codigo}"
                with mock.patch(POST, return_value=_respuesta(codigo, detalle)):
                    aviso = webhooks.entregar(self._aviso())

                aviso.refresh_from_db()
                self.assertEqual(aviso.estado, estado)
                self.assertEqual(aviso.codigo_http, codigo)
                self.assertEqual(aviso.error, detalle or "")
                self.assertIsNotNone(aviso.enviado_en)

    def test_timeout_y_sin_conexion_quedan_fallidos_sin_codigo(self):
        for error in (requests.Timeout("lento"), requests.ConnectionError("caído")):
            with self.subTest(error=type(error).__name__):
                with mock.patch(POST, side_effect=error):
                    aviso = webhooks.entregar(self._aviso())

                self.assertEqual(aviso.estado, WebhookAviso.Estado.FALLIDO)
                self.assertIsNone(aviso.codigo_http)
                self.assertIn(type(error).__name__, aviso.error)

    def test_no_reintenta(self):
        with mock.patch(POST, return_value=_respuesta(503, "caído")) as post:
            aviso = self._aviso()
            webhooks.enviar_pendientes([aviso.pk])
            webhooks.enviar_pendientes([aviso.pk])

        self.assertEqual(post.call_count, 1)


class DisparadoresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.emisor = datos["emisor"]
        cls.emisor.referencia_externa = "12"
        cls.emisor.save(update_fields=["referencia_externa"])
        cls.validacion = Webhook.objects.create(
            emisor=cls.emisor, nombre="validación", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )
        cls.notificacion = Webhook.objects.create(
            emisor=cls.emisor, nombre="notificación", url="https://torio.co/hook",
            estado_notificado=True, secreto="s3creto",
        )

    def _validar(self):
        self.documento.estado = DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.ACEPTADO)
        self.documento.fecha_validacion = datetime.datetime(
            2026, 9, 21, 10, 15, tzinfo=ZoneInfo("America/Bogota"),
        )
        self.documento.cufe_cude = "cufe-de-prueba"
        self.documento.save()
        registrar_cambio_de_estado(self.documento, DocumentoEstado.Nombre.ENVIADO, {})

    def test_validarse_avisa_solo_a_los_webhooks_de_validacion(self):
        with mock.patch(POST, return_value=_respuesta(200)) as post, \
                self.captureOnCommitCallbacks(execute=True):
            self._validar()

        aviso = WebhookAviso.objects.get()
        self.assertEqual(aviso.webhook, self.validacion)
        self.assertEqual(aviso.estado, WebhookAviso.Estado.ENTREGADO)
        self.assertEqual(json.loads(aviso.cuerpo), {
            "tipo": "validacion",
            "cliente": 12,
            "documento": str(self.documento.pk),
            "fecha_validacion": "2026-09-21T10:15:00-05:00",
            "cufe": "cufe-de-prueba",
        })
        self.assertEqual(post.call_args.kwargs["data"], aviso.cuerpo.encode())

    def _respondido(self):
        self.documento.refresh_from_db()
        return self.documento.respuesta_validado

    def test_con_un_200_queda_respondido(self):
        with mock.patch(POST, return_value=_respuesta(200)), \
                self.captureOnCommitCallbacks(execute=True):
            self._validar()

        self.assertTrue(self._respondido())

    def test_con_un_409_no_queda_respondido(self):
        with mock.patch(POST, return_value=_respuesta(409, "ya estaba validado")), \
                self.captureOnCommitCallbacks(execute=True):
            self._validar()

        self.assertEqual(WebhookAviso.objects.get().estado, WebhookAviso.Estado.ENTREGADO)
        self.assertFalse(self._respondido())

    def test_el_aviso_sale_despues_de_confirmar(self):
        with mock.patch(POST, return_value=_respuesta(200)) as post, \
                self.captureOnCommitCallbacks(execute=False) as pendientes:
            self._validar()

            post.assert_not_called()
            self.assertFalse(WebhookAviso.objects.exists())
        self.assertEqual(len(pendientes), 1)
        self.assertFalse(self._respondido())

    def test_no_avisa_si_ya_estaba_respondido(self):
        # Otra petición —el endpoint— pudo marcarlo antes de que corriera esto.
        with mock.patch(POST) as post, \
                self.captureOnCommitCallbacks(execute=False) as pendientes:
            self._validar()
        type(self.documento).objects.filter(pk=self.documento.pk).update(respuesta_validado=True)
        for callback in pendientes:
            callback()

        post.assert_not_called()
        self.assertFalse(WebhookAviso.objects.exists())

    def test_torio_caido_no_tumba_la_validacion(self):
        with mock.patch(POST, side_effect=requests.ConnectionError("caído")), \
                self.captureOnCommitCallbacks(execute=True):
            self._validar()

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado.nombre, DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(WebhookAviso.objects.get().estado, WebhookAviso.Estado.FALLIDO)
        self.assertFalse(self.documento.respuesta_validado)

    def test_un_error_inesperado_tampoco_sale(self):
        with mock.patch.object(webhooks, "entregar", side_effect=RuntimeError("roto")), \
                self.assertLogs("apps.emisores.servicios.webhooks", "ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            self._validar()

        self.assertEqual(WebhookAviso.objects.get().estado, WebhookAviso.Estado.PENDIENTE)
        self.assertFalse(self._respondido())

    def test_notificarse_avisa_una_sola_vez(self):
        with mock.patch(POST, return_value=_respuesta(200)) as post, \
                self.captureOnCommitCallbacks(execute=True):
            marcar_notificado(self.documento)
            marcar_notificado(self.documento)

        aviso = WebhookAviso.objects.get()
        self.assertEqual(aviso.webhook, self.notificacion)
        self.assertEqual(aviso.tipo, WebhookAviso.Tipo.NOTIFICACION)
        self.assertEqual(post.call_count, 1)
        # Notificar no toca la respuesta de validado.
        self.assertFalse(self._respondido())

    def test_sin_secreto_no_se_manda(self):
        Webhook.objects.filter(pk=self.validacion.pk).update(secreto="")

        with mock.patch(POST) as post, self.captureOnCommitCallbacks(execute=True):
            self._validar()

        post.assert_not_called()
        aviso = WebhookAviso.objects.get()
        self.assertEqual(aviso.estado, WebhookAviso.Estado.FALLIDO)
        self.assertEqual(aviso.error, webhooks.MENSAJE_SIN_SECRETO)
        self.assertFalse(self._respondido())

    def test_sin_referencia_externa_no_se_manda(self):
        self.emisor.referencia_externa = ""
        self.emisor.save(update_fields=["referencia_externa"])
        self.documento.emisor.refresh_from_db()

        with mock.patch(POST) as post, self.captureOnCommitCallbacks(execute=True):
            self._validar()

        post.assert_not_called()
        self.assertEqual(WebhookAviso.objects.get().error, webhooks.MENSAJE_SIN_REFERENCIA)

    def test_sin_webhooks_queda_respondido_sin_enviar(self):
        Webhook.objects.all().delete()

        with mock.patch(POST) as post, self.captureOnCommitCallbacks(execute=True):
            self._validar()

        post.assert_not_called()
        self.assertFalse(WebhookAviso.objects.exists())
        self.assertTrue(self._respondido())


class NominaSinAvisoTests(TestCase):
    # Aparte: el helper de nómina crea sus propios catálogos y chocaría con los
    # del documento de factura.
    def test_la_nomina_no_avisa(self):
        from apps.nomina.tests_utils import crear_nomina

        nomina, base = crear_nomina()
        Webhook.objects.create(
            emisor=base["emisor"], nombre="torio", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )
        nomina.estado = DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.ACEPTADO)
        nomina.save(update_fields=["estado"])

        with mock.patch(POST) as post, self.captureOnCommitCallbacks(execute=True):
            registrar_cambio_de_estado(nomina, DocumentoEstado.Nombre.ENVIADO, {})

        post.assert_not_called()
        self.assertFalse(WebhookAviso.objects.exists())


class WebhookAvisoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.usuario = get_user_model().objects.create_user(email="avisos@nobelio.co", password="x")
        cls.usuario.emisores.add(datos["emisor"])
        propio = Webhook.objects.create(emisor=datos["emisor"], nombre="propio", url="https://a.co/h")
        ajeno = Webhook.objects.create(
            emisor=_crear_emisor(datos["catalogos"], nit="800199436"), nombre="ajeno",
            url="https://b.co/h",
        )
        for webhook, estado in ((propio, "fallido"), (ajeno, "entregado")):
            WebhookAviso.objects.create(
                webhook=webhook, documento=cls.documento, tipo="validacion",
                cuerpo="{}", estado=estado, codigo_http=503 if estado == "fallido" else 200,
            )

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def test_lista_solo_los_del_alcance_con_el_motivo(self):
        resp = self.client.get("/api/emisores/webhook-aviso/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(resp.data["results"][0]["codigo_http"], 503)

    def test_filtra_por_estado(self):
        resp = self.client.get("/api/emisores/webhook-aviso/?estado=entregado")

        self.assertEqual(resp.data["results"], [])

    def test_es_de_solo_lectura(self):
        resp = self.client.post("/api/emisores/webhook-aviso/", {}, format="json")

        self.assertEqual(resp.status_code, 405)


class PruebaTests(TestCase):
    """``probar``: el aviso `prueba` a la URL del webhook, sin dejar rastro."""

    @classmethod
    def setUpTestData(cls):
        cls.emisor = crear_documento_factura()["emisor"]
        cls.emisor.referencia_externa = "12"
        cls.emisor.save(update_fields=["referencia_externa"])
        cls.webhook = Webhook.objects.create(
            emisor=cls.emisor, nombre="torio", url="https://torio.co/hook", secreto="s3creto",
        )

    def test_manda_tipo_y_cliente_firmados_a_la_url_del_webhook(self):
        with mock.patch(POST, return_value=_respuesta(200, "Aviso de prueba recibido.")) as post, \
                mock.patch("apps.emisores.servicios.webhooks.time.time", return_value=1790000000):
            resultado = webhooks.probar(self.webhook)

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://torio.co/hook")
        self.assertEqual(kwargs["data"], b'{"tipo": "prueba", "cliente": 12}')
        esperada = webhooks.firmar("s3creto", "1790000000", kwargs["data"])
        self.assertEqual(kwargs["headers"]["X-Rededoc-Firma"], esperada)
        self.assertEqual(kwargs["headers"]["X-Rededoc-Fecha"], "1790000000")
        self.assertTrue(resultado["entregado"])
        self.assertEqual(resultado["codigo_http"], 200)
        self.assertEqual(resultado["detalle"], "Aviso de prueba recibido.")
        self.assertIsInstance(resultado["duracion_ms"], int)

    def test_no_deja_avisos(self):
        with mock.patch(POST, return_value=_respuesta(200)):
            webhooks.probar(self.webhook)

        self.assertFalse(WebhookAviso.objects.exists())

    def test_solo_el_200_cuenta_como_entregado(self):
        for codigo, detalle in ((401, "Firma inválida."), (404, "El cliente no existe."),
                                (409, "ya"), (500, "")):
            with self.subTest(codigo=codigo), \
                    mock.patch(POST, return_value=_respuesta(codigo, detalle)):
                resultado = webhooks.probar(self.webhook)
                self.assertFalse(resultado["entregado"])
                self.assertEqual(resultado["codigo_http"], codigo)
                if detalle:
                    self.assertEqual(resultado["detalle"], detalle)

    def test_sin_conexion_no_hay_codigo_y_va_el_motivo(self):
        with mock.patch(POST, side_effect=requests.ConnectTimeout("tarde")):
            resultado = webhooks.probar(self.webhook)

        self.assertFalse(resultado["entregado"])
        self.assertIsNone(resultado["codigo_http"])
        self.assertIn("ConnectTimeout", resultado["detalle"])

    def _no_se_manda(self, mensaje):
        webhook = Webhook.objects.select_related("emisor").get(pk=self.webhook.pk)
        with mock.patch(POST) as post, \
                self.assertRaisesMessage(webhooks.PruebaImposible, mensaje):
            webhooks.probar(webhook)
        post.assert_not_called()

    def test_sin_secreto_no_se_manda(self):
        Webhook.objects.filter(pk=self.webhook.pk).update(secreto="")

        self._no_se_manda(webhooks.MENSAJE_SIN_SECRETO)

    def test_sin_referencia_externa_no_se_manda(self):
        type(self.emisor).objects.filter(pk=self.emisor.pk).update(referencia_externa=" ")

        self._no_se_manda(webhooks.MENSAJE_SIN_REFERENCIA)


class WebhookProbarAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.emisor = datos["emisor"]
        cls.emisor.referencia_externa = "12"
        cls.emisor.save(update_fields=["referencia_externa"])
        cls.webhook = Webhook.objects.create(
            emisor=cls.emisor, nombre="torio", url="https://torio.co/hook", secreto="s3creto",
        )
        cls.ajeno = Webhook.objects.create(
            emisor=_crear_emisor(datos["catalogos"], nit="800199436"), nombre="ajeno",
            url="https://ajeno.co/hook", secreto="otro",
        )
        cls.usuario = get_user_model().objects.create_user(email="probar@nobelio.co", password="x")
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _probar(self, webhook):
        return self.client.post(f"/api/emisores/webhook/{webhook.pk}/probar/")

    def test_devuelve_lo_que_respondio_el_receptor(self):
        with mock.patch(POST, return_value=_respuesta(200, "Aviso de prueba recibido.")):
            resp = self._probar(self.webhook)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            set(resp.data), {"entregado", "codigo_http", "detalle", "duracion_ms"},
        )
        self.assertTrue(resp.data["entregado"])

    def test_un_fallo_del_receptor_sigue_siendo_200(self):
        with mock.patch(POST, return_value=_respuesta(401, "Firma inválida.")):
            resp = self._probar(self.webhook)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["entregado"])
        self.assertEqual(resp.data["codigo_http"], 401)
        self.assertEqual(resp.data["detalle"], "Firma inválida.")

    def test_sin_secreto_es_400_y_no_se_manda(self):
        Webhook.objects.filter(pk=self.webhook.pk).update(secreto="")

        with mock.patch(POST) as post:
            resp = self._probar(self.webhook)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["detail"], webhooks.MENSAJE_SIN_SECRETO)
        post.assert_not_called()

    def test_uno_de_otra_cuenta_responde_como_inexistente(self):
        with mock.patch(POST) as post:
            resp = self._probar(self.ajeno)

        self.assertEqual(resp.status_code, 404)
        post.assert_not_called()

    def test_tiene_tope_propio(self):
        # Las tasas se parchean en la clase: DRF las lee al importar (ver
        # apps/seguridad/tests_limites.py).
        cache.clear()
        self.addCleanup(cache.clear)
        tasas = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "webhook_prueba": "1/min"}
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", tasas), \
                mock.patch(POST, return_value=_respuesta(200)):
            primera = self._probar(self.webhook)
            segunda = self._probar(self.webhook)

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 429)
