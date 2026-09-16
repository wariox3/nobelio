"""El límite de peticiones por credencial.

La suite corre con las tasas en `None` (ver `config/settings/test.py`), así que
estas pruebas las fijan a mano y limpian la caché, que es donde el contador
vive.
"""
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from apps.documentos.tests_utils import crear_usuario
from apps.seguridad.limites import LimitePorCredencial
from apps.seguridad.models import LlaveApi, Usuario

URL = "/api/emisores/emisor/"


def _tasas(user=None, anon=None):
    """Fija las tasas para un bloque de código.

    Se parchea el atributo de clase y no los settings: DRF resuelve
    `THROTTLE_RATES` **una vez, al importar** el módulo de throttling, así que
    un `override_settings` sobre `REST_FRAMEWORK` no llega a tiempo y la prueba
    pasaría sin comprobar nada.
    """
    return mock.patch.object(
        LimitePorCredencial, "THROTTLE_RATES", {"user": user, "anon": anon}
    )


class LimitePorCredencialTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.usuario = crear_usuario(nombre="RedDoc ERP")
        self.usuario = Usuario.objects.create_user(
            email="humano@nobelio.co", password="ClaveSegura123"
        )

    def tearDown(self):
        cache.clear()

    def _api_key(self, **kwargs):
        _, clave = LlaveApi.generar(nombre="ERP", **kwargs)
        return {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

    def test_la_api_key_se_cuenta_y_acaba_recibiendo_429(self):
        """El throttle de DRF no servía: pedía `request.user.pk` y reventaba."""
        with _tasas(user="2/hour"):
            cabecera = self._api_key(usuario=self.usuario)
            self.assertEqual(self.client.get(URL, **cabecera).status_code, 200)
            self.assertEqual(self.client.get(URL, **cabecera).status_code, 200)
            tercera = self.client.get(URL, **cabecera)

        self.assertEqual(tercera.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_cada_llave_lleva_su_propia_cuenta(self):
        """Estrangular una integración no puede frenar a las demás.

        Por eso la identidad es el id de la llave y no el de la cuenta: una
        cuenta puede tener varias, y un bucle roto en una no debe dejar sin
        servicio a las otras.
        """
        with _tasas(user="1/hour"):
            primera = self._api_key(usuario=self.usuario)
            segunda = self._api_key(usuario=self.usuario)

            self.assertEqual(self.client.get(URL, **primera).status_code, 200)
            self.assertEqual(
                self.client.get(URL, **primera).status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            # La otra llave de la misma cuenta sigue pasando.
            self.assertEqual(self.client.get(URL, **segunda).status_code, 200)

    def test_el_usuario_humano_tambien_se_cuenta(self):
        with _tasas(user="1/hour"):
            self.client.force_authenticate(self.usuario)
            self.assertEqual(self.client.get(URL).status_code, 200)
            self.assertEqual(
                self.client.get(URL).status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

    def test_sin_credencial_no_lo_cuenta_este_throttle(self):
        """De lo anónimo se ocupa `LimiteAnonimo`, no este."""
        peticion = type("P", (), {"user": None})()
        self.assertIsNone(
            LimitePorCredencial().get_cache_key(peticion, view=None)
        )


class LimitePorAmbitoTests(APITestCase):
    """Las vistas autenticadas con `throttle_scope`, llamadas con API Key."""

    URL = "/api/seguridad/mfa/enrolar/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.usuario = Usuario.objects.create_user(
            email="humano@nobelio.co", password="ClaveSegura123"
        )

    def _tasas(self, **rates):
        combinados = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], **rates}
        return mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", combinados)

    def _api_key(self):
        _, clave = LlaveApi.generar(nombre="ERP", usuario=self.usuario)
        return {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

    def test_la_api_key_se_cuenta_en_vez_de_dar_500(self):
        """El `ScopedRateThrottle` de DRF pedía `request.user.pk` y reventaba."""
        with self._tasas(mfa_gestion="1/hour"):
            cabecera = self._api_key()
            primera = self.client.post(self.URL, {}, format="json", **cabecera)
            segunda = self.client.post(self.URL, {}, format="json", **cabecera)

        self.assertNotEqual(primera.status_code, 429)
        self.assertLess(primera.status_code, 500)
        self.assertEqual(segunda.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_la_llave_y_la_persona_llevan_cuentas_distintas(self):
        with self._tasas(mfa_gestion="1/hour"):
            cabecera = self._api_key()
            self.client.post(self.URL, {}, format="json", **cabecera)
            self.client.force_authenticate(self.usuario)
            humano = self.client.post(self.URL, {}, format="json")

        self.assertNotEqual(humano.status_code, 429)
