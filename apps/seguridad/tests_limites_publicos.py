"""Los topes de las rutas públicas frenan de verdad.

Se activan con `override_settings` porque en `config.settings.test` están todos
en `None`: el resto de la suite no debe pelearse con ellos.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.seguridad import verificacion

Usuario = get_user_model()


def con_topes(**rates):
    """Activa solo los topes indicados; el resto siguen desactivados.

    No sirve `override_settings(REST_FRAMEWORK=...)`: DRF copia las tarifas a
    `SimpleRateThrottle.THROTTLE_RATES` cuando se importa la clase, así que
    cambiar el setting después no las mueve. Se parchea el atributo, que es de
    donde leen todas las subclases.
    """
    from django.conf import settings
    from rest_framework.throttling import SimpleRateThrottle

    combinados = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], **rates}
    return patch.object(SimpleRateThrottle, "THROTTLE_RATES", combinados)


class LimitesPublicosTests(TestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @con_topes(registro="2/hour")
    def test_el_registro_se_corta_por_ip(self):
        url = reverse("registro")
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            for i in range(2):
                r = self.client.post(url, {
                    "email": f"ana{i}@empresa.co",
                    "password": "una-clave-larga-y-decente",
                }, content_type="application/json")
                self.assertEqual(r.status_code, 201)
            r = self.client.post(url, {
                "email": "ana9@empresa.co",
                "password": "una-clave-larga-y-decente",
            }, content_type="application/json")
        self.assertEqual(r.status_code, 429)

    @con_topes(reenvio_correo="1/hour")
    def test_el_reenvio_se_corta_por_destinatario(self):
        """Aunque cambie la IP: lo que se protege es la bandeja de la víctima."""
        Usuario.objects.create_user(
            email="victima@empresa.co", password="una-clave-larga-y-decente"
        )
        url = reverse("registro-reenviar")
        cuerpo = {"email": "victima@empresa.co"}
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            primera = self.client.post(url, cuerpo, content_type="application/json",
                                       REMOTE_ADDR="10.0.0.1")
            segunda = self.client.post(url, cuerpo, content_type="application/json",
                                       REMOTE_ADDR="10.0.0.99")
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 429)

    @con_topes(reenvio_correo="1/hour")
    def test_el_tope_por_correo_no_alcanza_a_otro_destinatario(self):
        url = reverse("registro-reenviar")
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            self.client.post(url, {"email": "uno@empresa.co"},
                             content_type="application/json")
            otro = self.client.post(url, {"email": "otro@empresa.co"},
                                    content_type="application/json")
        self.assertEqual(otro.status_code, 200)

    @con_topes(login_correo="2/hour")
    def test_el_login_se_corta_por_cuenta_aunque_cambie_la_ip(self):
        """Un ataque de contraseñas repartido entre IP no debe pasar."""
        Usuario.objects.create_user(
            email="ana@empresa.co", password="una-clave-larga-y-decente",
            is_verified=True,
        )
        url = reverse("token_obtain_pair")
        malas = {"email": "ana@empresa.co", "password": "equivocada"}
        for ip in ("10.0.0.1", "10.0.0.2"):
            r = self.client.post(url, malas, content_type="application/json",
                                 REMOTE_ADDR=ip)
            self.assertEqual(r.status_code, 401, r.data)
        r = self.client.post(url, malas, content_type="application/json",
                             REMOTE_ADDR="10.0.0.3")
        self.assertEqual(r.status_code, 429)

    @con_topes(registro_rafaga="1/min", registro="100/hour")
    def test_la_rafaga_corta_aunque_el_sostenido_dé_de_sobra(self):
        url = reverse("registro")
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            primera = self.client.post(url, {
                "email": "uno@empresa.co", "password": "una-clave-larga-y-decente",
            }, content_type="application/json")
            segunda = self.client.post(url, {
                "email": "dos@empresa.co", "password": "una-clave-larga-y-decente",
            }, content_type="application/json")
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 429)


class IdentidadDetrasDeProxyTests(TestCase):
    """`NUM_PROXIES` decide si los topes por IP son falsificables."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @con_topes(registro="1/hour")
    def test_no_se_saltan_mandando_x_forwarded_for(self):
        """Con NUM_PROXIES=0 la cabecera del cliente se ignora."""
        url = reverse("registro")
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            primera = self.client.post(url, {
                "email": "uno@empresa.co", "password": "una-clave-larga-y-decente",
            }, content_type="application/json", HTTP_X_FORWARDED_FOR="1.2.3.4")
            segunda = self.client.post(url, {
                "email": "dos@empresa.co", "password": "una-clave-larga-y-decente",
            }, content_type="application/json", HTTP_X_FORWARDED_FOR="5.6.7.8")
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 429)
