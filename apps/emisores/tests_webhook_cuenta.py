"""El webhook de cuenta: vale para todos sus emisores y se suma a los de cada uno."""
import datetime
import json
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.models import DocumentoEstado
from apps.documentos.tests_utils import crear_documento_factura, crear_usuario
from apps.emisores.models import Cuenta, Webhook, WebhookAviso
from apps.emisores.serializers.webhook import (
    MENSAJE_CUENTA_INMUTABLE,
    MENSAJE_CUENTA_SOLO_ALCANCE_TOTAL,
    MENSAJE_EMISOR_O_CUENTA,
)
from apps.emisores.servicios import webhooks
from apps.emisores.tests_software import _crear_emisor
from apps.nucleo.tests_utils import errores_por_campo
from apps.seguridad.models import LlaveApi

URL = "/api/emisores/webhook/"
URL_CUENTAS = "/api/emisores/cuenta/"
POST = "apps.emisores.servicios.webhooks.requests.post"


def _respuesta(codigo):
    respuesta = mock.Mock(status_code=codigo, text="")
    respuesta.json.side_effect = ValueError("sin cuerpo")
    return respuesta


def _datos():
    """Un documento aceptado de un emisor con cuenta y referencia externa."""
    datos = crear_documento_factura()
    cuenta = Cuenta.objects.create(nombre="torio")
    emisor = datos["emisor"]
    emisor.cuenta = cuenta
    emisor.referencia_externa = "12"
    emisor.save(update_fields=["cuenta", "referencia_externa"])
    documento = datos["documento"]
    documento.estado = DocumentoEstado.objects.get(
        nombre=DocumentoEstado.Nombre.ACEPTADO
    )
    documento.fecha_validacion = timezone.now()
    documento.cufe_cude = "cufe-de-prueba"
    documento.save()
    return datos, cuenta, emisor, documento


class WebhookDeCuentaModeloTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.cuenta, cls.emisor, _ = _datos()

    def _crear(self, **dueno):
        with transaction.atomic():
            return Webhook.objects.create(
                nombre="ERP", url="https://erp.co/hook", **dueno,
            )

    def test_es_de_un_emisor_o_de_una_cuenta(self):
        self._crear(emisor=self.emisor)
        self._crear(cuenta=self.cuenta)

    def test_no_es_de_los_dos_ni_de_ninguno(self):
        for dueno in ({"emisor": self.emisor, "cuenta": self.cuenta}, {}):
            with self.subTest(dueno=list(dueno)):
                with self.assertRaises(IntegrityError):
                    self._crear(**dueno)

    def test_al_emisor_le_aplican_los_suyos_y_los_de_su_cuenta(self):
        propio = self._crear(emisor=self.emisor)
        de_cuenta = self._crear(cuenta=self.cuenta)
        self._crear(cuenta=Cuenta.objects.create(nombre="otra"))

        self.assertEqual(set(Webhook.de(self.emisor)), {propio, de_cuenta})

    def test_al_emisor_sin_cuenta_solo_los_suyos(self):
        self._crear(cuenta=self.cuenta)
        self.emisor.cuenta = None
        self.emisor.save(update_fields=["cuenta"])

        self.assertFalse(Webhook.de(self.emisor).exists())


class AvisosDeCuentaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.cuenta, cls.emisor, cls.documento = _datos()
        cls.de_cuenta = Webhook.objects.create(
            cuenta=cls.cuenta, nombre="torio", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )

    def test_avisa_al_del_emisor_y_al_de_la_cuenta(self):
        propio = Webhook.objects.create(
            emisor=self.emisor, nombre="propio", url="https://propio.co/hook",
            estado_validado=True, secreto="otro",
        )

        with mock.patch(POST, return_value=_respuesta(200)) as post:
            respondido, avisos = webhooks.responder_validado(self.documento)

        self.assertTrue(respondido)
        self.assertEqual({a.webhook for a in avisos}, {propio, self.de_cuenta})
        self.assertEqual(
            {llamada.args[0] for llamada in post.call_args_list},
            {"https://propio.co/hook", "https://torio.co/hook"},
        )

    def test_el_aviso_de_la_cuenta_lleva_el_cliente_del_emisor(self):
        with mock.patch(POST, return_value=_respuesta(200)) as post:
            webhooks.responder_validado(self.documento)

        cuerpo = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(cuerpo["cliente"], 12)
        self.assertEqual(cuerpo["documento"], str(self.documento.pk))

    def test_basta_el_200_del_de_la_cuenta(self):
        Webhook.objects.create(
            emisor=self.emisor, nombre="propio", url="https://propio.co/hook",
            estado_validado=True, secreto="otro",
        )
        por_url = {"https://propio.co/hook": 503, "https://torio.co/hook": 200}

        with mock.patch(POST, side_effect=lambda url, **_: _respuesta(por_url[url])):
            respondido, _ = webhooks.responder_validado(self.documento)

        self.assertTrue(respondido)

    def test_con_solo_el_de_la_cuenta_ya_no_se_marca_sin_enviar(self):
        with mock.patch(POST, return_value=_respuesta(503)):
            respondido, avisos = webhooks.responder_validado(self.documento)

        self.assertFalse(respondido)
        self.assertEqual(len(avisos), 1)
        self.documento.refresh_from_db()
        self.assertFalse(self.documento.respuesta_validado)

    def test_el_de_otra_cuenta_no_recibe_nada(self):
        Webhook.objects.filter(pk=self.de_cuenta.pk).update(
            cuenta=Cuenta.objects.create(nombre="otra"),
        )

        with mock.patch(POST) as post:
            respondido, avisos = webhooks.responder_validado(self.documento)

        self.assertTrue(respondido)
        self.assertEqual(avisos, [])
        post.assert_not_called()
        self.assertFalse(WebhookAviso.objects.exists())


class WebhookDeCuentaAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos, cls.cuenta, cls.emisor, _ = _datos()
        cls.otro_emisor = _crear_emisor(datos["catalogos"], nit="800199436")
        cls.staff = crear_usuario(nombre="Staff", is_staff=True)
        cls.cliente = crear_usuario(nombre="Cliente")
        cls.cliente.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.staff)

    def _crear(self, **dueno):
        return self.client.post(
            URL,
            {"nombre": "torio", "url": "https://torio.co/hook",
             "estado_validado": True, "secreto": "s3creto", **dueno},
            format="json",
        )

    def _de_cuenta(self):
        return Webhook.objects.create(
            cuenta=self.cuenta, nombre="torio", url="https://torio.co/hook",
            secreto="s3creto",
        )

    def _con_llave(self, **opciones):
        _, clave = LlaveApi.generar(usuario=self.staff, nombre="talio", **opciones)
        # Un cliente nuevo y no `force_authenticate(None)`: ese cierra sesión,
        # y el proyecto no tiene la app de sesiones.
        self.client = self.client_class()
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {clave}")

    def _con_llave_global(self):
        self._con_llave(
            expira_en=timezone.now() + timedelta(days=30), alcance_global=True,
        )

    def test_el_staff_crea_uno_de_cuenta(self):
        resp = self._crear(cuenta=self.cuenta.pk)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["cuenta"], self.cuenta.pk)
        self.assertIsNone(resp.data["emisor"])
        self.assertNotIn("secreto", resp.data)

    def test_el_de_emisor_sale_con_cuenta_nula(self):
        resp = self._crear(emisor=self.emisor.pk)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["emisor"], self.emisor.pk)
        self.assertIsNone(resp.data["cuenta"])

    def test_exige_emisor_o_cuenta_y_no_los_dos(self):
        casos = (
            {},
            {"emisor": None, "cuenta": None},
            {"emisor": self.emisor.pk, "cuenta": self.cuenta.pk},
        )
        for dueno in casos:
            with self.subTest(dueno=dueno):
                resp = self._crear(**dueno)

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertIn(MENSAJE_EMISOR_O_CUENTA, resp.data["detail"])
        self.assertFalse(Webhook.objects.exists())

    def test_no_cambia_de_cuenta_ni_pasa_a_un_emisor(self):
        webhook = self._de_cuenta()
        otra = Cuenta.objects.create(nombre="otra")

        de_cuenta = self.client.patch(
            f"{URL}{webhook.pk}/", {"cuenta": otra.pk}, format="json",
        )
        a_emisor = self.client.patch(
            f"{URL}{webhook.pk}/", {"emisor": self.emisor.pk}, format="json",
        )

        self.assertEqual(de_cuenta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            errores_por_campo(de_cuenta)["cuenta"], [MENSAJE_CUENTA_INMUTABLE],
        )
        self.assertEqual(a_emisor.status_code, status.HTTP_400_BAD_REQUEST)
        webhook.refresh_from_db()
        self.assertEqual(webhook.cuenta, self.cuenta)
        self.assertIsNone(webhook.emisor)

    def test_se_actualiza_y_se_borra(self):
        webhook = self._de_cuenta()

        resp = self.client.patch(
            f"{URL}{webhook.pk}/", {"estado_notificado": True}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["estado_notificado"])

        resp = self.client.delete(f"{URL}{webhook.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_filtra_por_cuenta(self):
        webhook = self._de_cuenta()
        Webhook.objects.create(emisor=self.emisor, nombre="propio", url="https://p.co/h")

        resp = self.client.get(URL, {"cuenta": self.cuenta.pk})

        self.assertEqual([w["id"] for w in resp.data["results"]], [webhook.pk])

    def test_la_llave_global_los_crea_y_los_ve(self):
        self._con_llave_global()

        creado = self._crear(cuenta=self.cuenta.pk)
        lista = self.client.get(URL)

        self.assertEqual(creado.status_code, status.HTTP_201_CREATED, creado.data)
        self.assertEqual([w["id"] for w in lista.data["results"]], [creado.data["id"]])

    def test_quien_no_alcanza_todo_no_los_crea(self):
        self.client.force_authenticate(self.cliente)

        for dueno in ({"cuenta": self.cuenta.pk}, {"cuenta": self.cuenta.pk, "emisor": None}):
            with self.subTest(dueno=dueno):
                resp = self._crear(**dueno)

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(
                    errores_por_campo(resp)["cuenta"],
                    [MENSAJE_CUENTA_SOLO_ALCANCE_TOTAL],
                )
        self.assertFalse(Webhook.objects.exists())

    def test_quien_no_alcanza_todo_no_los_ve_ni_los_toca(self):
        webhook = self._de_cuenta()
        self.client.force_authenticate(self.cliente)

        self.assertEqual(self.client.get(URL).data["results"], [])
        self.assertEqual(
            self.client.get(f"{URL}{webhook.pk}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(f"{URL}{webhook.pk}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_la_llave_normal_tampoco(self):
        self._de_cuenta()
        self._con_llave()

        self.assertEqual(self.client.get(URL).data["results"], [])
        self.assertEqual(
            self._crear(cuenta=self.cuenta.pk).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    # --- probar --------------------------------------------------------------

    def _probar(self, webhook, **datos):
        return self.client.post(f"{URL}{webhook.pk}/probar/", datos, format="json")

    def test_se_prueba_con_un_emisor_de_la_cuenta(self):
        webhook = self._de_cuenta()

        with mock.patch(POST, return_value=_respuesta(200)) as post:
            resp = self._probar(webhook, emisor=self.emisor.pk)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["entregado"])
        self.assertEqual(
            json.loads(post.call_args.kwargs["data"]),
            {"tipo": "prueba", "cliente": 12},
        )

    def test_probar_sin_emisor_es_400(self):
        webhook = self._de_cuenta()

        with mock.patch(POST) as post:
            resp = self._probar(webhook)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(resp.data["detail"], webhooks.MENSAJE_PRUEBA_SIN_EMISOR)
        post.assert_not_called()

    def test_probar_con_un_emisor_de_fuera_de_la_cuenta_es_400(self):
        webhook = self._de_cuenta()

        with mock.patch(POST) as post:
            ajeno = self._probar(webhook, emisor=self.otro_emisor.pk)
            inexistente = self._probar(webhook, emisor=999999)

        for resp in (ajeno, inexistente):
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
            self.assertEqual(resp.data["detail"], webhooks.MENSAJE_PRUEBA_EMISOR_AJENO)
        post.assert_not_called()


class CuentasConLlaveTests(APITestCase):
    """Las cuentas las administra quien alcanza todos los emisores."""

    def setUp(self):
        self.staff = crear_usuario(nombre="Staff", is_staff=True)

    def _con_llave(self, **opciones):
        _, clave = LlaveApi.generar(usuario=self.staff, nombre="talio", **opciones)
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {clave}")

    def test_la_llave_global_lista_crea_y_borra(self):
        self._con_llave(
            expira_en=timezone.now() + timedelta(days=30), alcance_global=True,
        )

        creada = self.client.post(URL_CUENTAS, {"nombre": "torio"}, format="json")
        lista = self.client.get(URL_CUENTAS)
        borrada = self.client.delete(f"{URL_CUENTAS}{creada.data['id']}/")

        self.assertEqual(creada.status_code, status.HTTP_201_CREATED, creada.data)
        self.assertEqual(lista.status_code, status.HTTP_200_OK, lista.data)
        self.assertEqual(borrada.status_code, status.HTTP_204_NO_CONTENT)

    def test_la_llave_normal_no_entra(self):
        self._con_llave()

        self.assertEqual(
            self.client.get(URL_CUENTAS).status_code, status.HTTP_403_FORBIDDEN,
        )
