"""Recuperación de contraseña: el enlace, lo que quema y lo que no salta."""
from unittest.mock import patch

import pyotp
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.seguridad import mfa as servicio_mfa
from apps.seguridad import recuperacion as servicio
from apps.seguridad.models import METODO_TOTP, MfaUsuario

Usuario = get_user_model()

CLAVE_VIEJA = "una-clave-larga-y-decente"
CLAVE_NUEVA = "otra-clave-igual-de-larga"
LLAVE_MFA = "yYo2r6dK5b0pQqvW8xLZ3mJ1nT7sH4gCfE9uA0iRbXk="


class BaseRecuperacion(TestCase):
    def setUp(self):
        self.cliente = APIClient()
        self.usuario = Usuario.objects.create_user(
            email="ana@empresa.co", password=CLAVE_VIEJA, is_verified=True
        )
        self.url_pedir = reverse("token-recuperar")
        self.url_fijar = reverse("token-restablecer")

    def _token(self):
        return servicio.generar_token(self.usuario)


class PedirElEnlaceTests(BaseRecuperacion):

    def test_manda_el_correo_a_quien_existe(self):
        with patch.object(servicio, "enviar_recuperacion", return_value=True) as enviar:
            r = self.cliente.post(self.url_pedir, {"email": "ana@empresa.co"},
                                  format="json")
        self.assertEqual(r.status_code, 200)
        enviar.assert_called_once()

    def test_responde_igual_exista_o_no(self):
        """Si cambiara, sería un comprobador de quién está registrado."""
        with patch.object(servicio, "enviar_recuperacion", return_value=True):
            existe = self.cliente.post(self.url_pedir, {"email": "ana@empresa.co"},
                                       format="json")
            no_existe = self.cliente.post(self.url_pedir, {"email": "nadie@x.co"},
                                          format="json")
        self.assertEqual(existe.status_code, no_existe.status_code)
        self.assertEqual(existe.data, no_existe.data)

    def test_a_un_usuario_inactivo_no_se_le_manda(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        with patch.object(servicio, "enviar_recuperacion") as enviar:
            r = self.cliente.post(self.url_pedir, {"email": "ana@empresa.co"},
                                  format="json")
        self.assertEqual(r.status_code, 200)
        enviar.assert_not_called()


class RestablecerTests(BaseRecuperacion):

    def test_cambia_la_contrasena(self):
        r = self.cliente.post(self.url_fijar,
                              {"token": self._token(), "password": CLAVE_NUEVA},
                              format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(CLAVE_NUEVA))
        self.assertFalse(self.usuario.check_password(CLAVE_VIEJA))

    def test_el_enlace_se_quema_al_usarse(self):
        """Sin esto, el enlace sería una segunda llave de la cuenta."""
        token = self._token()
        self.cliente.post(self.url_fijar, {"token": token, "password": CLAVE_NUEVA},
                          format="json")
        segunda = self.cliente.post(
            self.url_fijar, {"token": token, "password": "una-tercera-clave-larga"},
            format="json",
        )
        self.assertEqual(segunda.status_code, 400)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(CLAVE_NUEVA))

    def test_cambiar_la_clave_por_otra_via_tambien_lo_quema(self):
        token = self._token()
        self.usuario.set_password("cambiada-desde-el-perfil")
        self.usuario.save(update_fields=["password"])
        r = self.cliente.post(self.url_fijar,
                              {"token": token, "password": CLAVE_NUEVA},
                              format="json")
        self.assertEqual(r.status_code, 400)

    def test_token_manipulado_se_rechaza(self):
        r = self.cliente.post(self.url_fijar,
                              {"token": "inventado", "password": CLAVE_NUEVA},
                              format="json")
        self.assertEqual(r.status_code, 400)

    def test_token_caducado_se_rechaza(self):
        token = self._token()
        with patch.object(servicio, "VIGENCIA_SEGUNDOS", -1):
            r = self.cliente.post(self.url_fijar,
                                  {"token": token, "password": CLAVE_NUEVA},
                                  format="json")
        self.assertEqual(r.status_code, 400)

    def test_una_clave_debil_se_rechaza(self):
        r = self.cliente.post(self.url_fijar,
                              {"token": self._token(), "password": "123"},
                              format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.data["errores"])
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(CLAVE_VIEJA))

    def test_los_mensajes_de_rechazo_no_se_distinguen(self):
        """Distinguir "caducó" de "ya se usó" revela que el enlace existió."""
        usado = self._token()
        self.cliente.post(self.url_fijar, {"token": usado, "password": CLAVE_NUEVA},
                          format="json")
        r_usado = self.cliente.post(self.url_fijar,
                                    {"token": usado, "password": CLAVE_NUEVA},
                                    format="json")
        r_falso = self.cliente.post(self.url_fijar,
                                    {"token": "inventado", "password": CLAVE_NUEVA},
                                    format="json")
        self.assertEqual(r_usado.data["detail"], r_falso.data["detail"])

    def test_restablecer_verifica_el_correo(self):
        """Recibir el enlace prueba que controla la dirección.

        Sin esto, quien se registró y nunca confirmó queda en un callejón sin
        salida: puede cambiar la contraseña pero el login le sigue diciendo que
        confirme un correo que ya no puede.
        """
        sin_verificar = Usuario.objects.create_user(
            email="beto@empresa.co", password=CLAVE_VIEJA
        )
        self.assertFalse(sin_verificar.is_verified)
        token = servicio.generar_token(sin_verificar)
        self.cliente.post(self.url_fijar, {"token": token, "password": CLAVE_NUEVA},
                          format="json")
        sin_verificar.refresh_from_db()
        self.assertTrue(sin_verificar.is_verified)


class LoQueArrastraTests(BaseRecuperacion):

    def test_cierra_las_sesiones_abiertas(self):
        """Si alguien entró con la clave robada, cambiarla tiene que echarlo."""
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        from rest_framework_simplejwt.tokens import RefreshToken

        RefreshToken.for_user(self.usuario)
        self.assertEqual(BlacklistedToken.objects.count(), 0)

        self.cliente.post(self.url_fijar,
                          {"token": self._token(), "password": CLAVE_NUEVA},
                          format="json")
        self.assertEqual(BlacklistedToken.objects.count(), 1)

    @override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
    def test_olvida_los_dispositivos_recordados(self):
        from apps.seguridad.models import MfaDispositivo

        servicio_mfa.recordar_dispositivo(self.usuario)
        self.assertEqual(MfaDispositivo.objects.count(), 1)

        self.cliente.post(self.url_fijar,
                          {"token": self._token(), "password": CLAVE_NUEVA},
                          format="json")
        self.assertEqual(MfaDispositivo.objects.count(), 0)


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class NoSaltaElSegundoFactorTests(BaseRecuperacion):

    def test_tras_restablecer_sigue_pidiendo_el_codigo(self):
        """El correo no puede ser la llave maestra: el MFA sigue en pie."""
        secreto = servicio_mfa.generar_secreto()
        MfaUsuario.objects.create(
            usuario=self.usuario, metodo=METODO_TOTP, activo=True,
            secreto=servicio_mfa.cifrar_secreto(secreto),
        )
        self.cliente.post(self.url_fijar,
                          {"token": self._token(), "password": CLAVE_NUEVA},
                          format="json")

        r = self.cliente.post(reverse("token_obtain_pair"),
                              {"email": "ana@empresa.co", "password": CLAVE_NUEVA},
                              format="json")
        self.assertTrue(r.data.get("mfa_requerido"))
        self.assertNotIn("access_token", r.cookies)

        # Y con el código sí entra: se restableció la clave, no el segundo factor.
        r = self.cliente.post(reverse("token-mfa"), {
            "mfa_token": r.data["mfa_token"], "codigo": pyotp.TOTP(secreto).now(),
        }, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("access_token", r.cookies)
