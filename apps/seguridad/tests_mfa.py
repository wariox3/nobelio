"""Pruebas del segundo factor: enrolamiento, ingreso en dos pasos y bordes."""
from unittest.mock import patch

import pyotp
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad import mfa as servicio
from apps.seguridad.models import METODO_CORREO, METODO_TOTP, MfaDesafio, MfaUsuario

Usuario = get_user_model()

CLAVE = "una-clave-larga-y-decente"
# Fernet válida y fija: las pruebas no deben depender del .env.
LLAVE_MFA = "fGakeTuAPXzhcAMK-5uYN7s_CF0vNz1gpVAmRFv2clk="


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class BaseMfa(TestCase):
    def setUp(self):
        self.cliente = APIClient()
        self.usuario = Usuario.objects.create_user(
            email="ana@empresa.co", password=CLAVE, is_verified=True
        )
        self.cliente.force_authenticate(user=self.usuario)

    def _enrolar_totp(self):
        r = self.cliente.post(reverse("mfa-enrolar"), {"metodo": METODO_TOTP},
                              format="json")
        return r.data["secreto"]

    def _activar_totp(self):
        secreto = self._enrolar_totp()
        codigo = pyotp.TOTP(secreto).now()
        self.cliente.post(reverse("mfa-confirmar"), {"codigo": codigo},
                          format="json")
        # Confirmar consumió la ventana de 30 s, así que ese mismo código ya no
        # sirve para entrar —que es justo lo que se quiere—. En la realidad pasa
        # el tiempo entre enrolar e ingresar; aquí se simula soltando el
        # contador. Que el consumo funcione lo prueba
        # `test_el_codigo_no_se_puede_reusar`.
        MfaUsuario.objects.filter(usuario=self.usuario).update(ultimo_contador=None)
        return secreto


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class EnrolamientoTests(BaseMfa):

    def test_enrolar_no_enciende_todavia(self):
        """Encenderlo antes de comprobar la app dejaría a la gente fuera."""
        self._enrolar_totp()
        self.assertFalse(MfaUsuario.objects.get(usuario=self.usuario).activo)

    def test_el_secreto_se_guarda_cifrado(self):
        secreto = self._enrolar_totp()
        guardado = MfaUsuario.objects.get(usuario=self.usuario).secreto
        self.assertNotEqual(guardado, secreto)
        self.assertEqual(servicio.descifrar_secreto(guardado), secreto)

    def test_confirmar_enciende_y_entrega_respaldos(self):
        secreto = self._enrolar_totp()
        r = self.cliente.post(reverse("mfa-confirmar"),
                              {"codigo": pyotp.TOTP(secreto).now()}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["activo"])
        self.assertEqual(len(r.data["codigos_respaldo"]), servicio.CANTIDAD_RESPALDO)
        self.assertTrue(MfaUsuario.objects.get(usuario=self.usuario).activo)

    def test_confirmar_con_codigo_malo_no_enciende(self):
        self._enrolar_totp()
        r = self.cliente.post(reverse("mfa-confirmar"), {"codigo": "000000"},
                              format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MfaUsuario.objects.get(usuario=self.usuario).activo)

    def test_desactivar_exige_la_contrasena(self):
        self._activar_totp()
        r = self.cliente.post(reverse("mfa-desactivar"), {"password": "otra"},
                              format="json")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(MfaUsuario.objects.filter(usuario=self.usuario).exists())

        r = self.cliente.post(reverse("mfa-desactivar"), {"password": CLAVE},
                              format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(MfaUsuario.objects.filter(usuario=self.usuario).exists())


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class IngresoEnDosPasosTests(BaseMfa):

    def setUp(self):
        super().setUp()
        self.secreto = self._activar_totp()
        self.cliente = APIClient()  # sin sesión: la API no tiene logout()

    def _paso_uno(self):
        return self.cliente.post(reverse("token_obtain_pair"),
                                 {"email": "ana@empresa.co", "password": CLAVE},
                                 format="json")

    def test_el_primer_paso_no_entrega_sesion(self):
        """Si el paso uno diera cookies, el segundo factor sería decorativo."""
        r = self._paso_uno()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["mfa_requerido"])
        self.assertNotIn("access_token", r.cookies)

    def test_el_segundo_paso_entrega_la_sesion(self):
        token = self._paso_uno().data["mfa_token"]
        r = self.cliente.post(reverse("token-mfa"), {
            "mfa_token": token, "codigo": pyotp.TOTP(self.secreto).now(),
        }, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("access_token", r.cookies)

    def test_codigo_malo_no_entrega_sesion(self):
        token = self._paso_uno().data["mfa_token"]
        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token, "codigo": "000000"},
                              format="json")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("access_token", r.cookies)

    def test_el_codigo_no_se_puede_reusar(self):
        """Sin esto, un código interceptado sirve durante 90 segundos."""
        codigo = pyotp.TOTP(self.secreto).now()
        token = self._paso_uno().data["mfa_token"]
        self.cliente.post(reverse("token-mfa"),
                          {"mfa_token": token, "codigo": codigo}, format="json")

        self.cliente.cookies.clear()
        token2 = self._paso_uno().data["mfa_token"]
        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token2, "codigo": codigo}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_el_desafio_se_agota_a_los_cinco_intentos(self):
        token = self._paso_uno().data["mfa_token"]
        for _ in range(servicio.MAX_INTENTOS):
            self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token, "codigo": "000000"}, format="json")
        # Ni siquiera con el código correcto: el desafío ya está quemado.
        r = self.cliente.post(reverse("token-mfa"), {
            "mfa_token": token, "codigo": pyotp.TOTP(self.secreto).now(),
        }, format="json")
        self.assertEqual(r.status_code, 401)
        self.assertIn("intentos", r.data["detail"])

    def test_el_desafio_no_se_puede_consumir_dos_veces(self):
        token = self._paso_uno().data["mfa_token"]
        codigo = pyotp.TOTP(self.secreto).now()
        self.cliente.post(reverse("token-mfa"),
                          {"mfa_token": token, "codigo": codigo}, format="json")
        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token, "codigo": codigo}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_el_desafio_caducado_no_sirve(self):
        token = self._paso_uno().data["mfa_token"]
        MfaDesafio.objects.update(expira=timezone.now() - servicio.DURACION_DESAFIO)
        r = self.cliente.post(reverse("token-mfa"), {
            "mfa_token": token, "codigo": pyotp.TOTP(self.secreto).now(),
        }, format="json")
        self.assertEqual(r.status_code, 401)

    def test_un_token_manipulado_no_sirve(self):
        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": "inventado", "codigo": "000000"},
                              format="json")
        self.assertEqual(r.status_code, 401)


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class CodigosRespaldoTests(BaseMfa):

    def test_un_respaldo_entra_y_se_consume(self):
        secreto = self._enrolar_totp()
        respaldos = self.cliente.post(
            reverse("mfa-confirmar"), {"codigo": pyotp.TOTP(secreto).now()},
            format="json",
        ).data["codigos_respaldo"]
        self.cliente = APIClient()  # sin sesión: la API no tiene logout()

        token = self.cliente.post(reverse("token_obtain_pair"),
                                  {"email": "ana@empresa.co", "password": CLAVE},
                                  format="json").data["mfa_token"]
        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token, "codigo": respaldos[0]},
                              format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["uso_codigo_respaldo"])
        self.assertEqual(servicio.respaldos_restantes(self.usuario),
                         servicio.CANTIDAD_RESPALDO - 1)

    def test_el_mismo_respaldo_no_sirve_dos_veces(self):
        secreto = self._enrolar_totp()
        respaldos = self.cliente.post(
            reverse("mfa-confirmar"), {"codigo": pyotp.TOTP(secreto).now()},
            format="json",
        ).data["codigos_respaldo"]
        self.assertTrue(servicio._consumir_respaldo(self.usuario, respaldos[0]))
        self.assertFalse(servicio._consumir_respaldo(self.usuario, respaldos[0]))

    def test_en_la_base_solo_queda_el_hash(self):
        from apps.seguridad.models import MfaCodigoRespaldo

        secreto = self._enrolar_totp()
        respaldos = self.cliente.post(
            reverse("mfa-confirmar"), {"codigo": pyotp.TOTP(secreto).now()},
            format="json",
        ).data["codigos_respaldo"]
        guardados = set(
            MfaCodigoRespaldo.objects.values_list("hash_codigo", flat=True)
        )
        self.assertFalse(set(respaldos) & guardados)


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class DispositivoRecordadoTests(BaseMfa):

    def test_recordar_permite_saltarse_el_segundo_paso(self):
        secreto = self._activar_totp()
        self.cliente = APIClient()  # sin sesión: la API no tiene logout()

        token = self.cliente.post(reverse("token_obtain_pair"),
                                  {"email": "ana@empresa.co", "password": CLAVE},
                                  format="json").data["mfa_token"]
        self.cliente.post(reverse("token-mfa"), {
            "mfa_token": token,
            "codigo": pyotp.TOTP(secreto).now(),
            "recordar_dispositivo": True,
        }, format="json")

        # Mismo navegador: la cookie de dispositivo sigue puesta.
        r = self.cliente.post(reverse("token_obtain_pair"),
                              {"email": "ana@empresa.co", "password": CLAVE},
                              format="json")
        self.assertNotIn("mfa_requerido", r.data)
        self.assertIn("access_token", r.cookies)

    def test_la_cookie_de_otro_no_sirve(self):
        """El dispositivo se valida contra el usuario que ya probó su clave."""
        self._activar_totp()
        otro = Usuario.objects.create_user(
            email="beto@empresa.co", password=CLAVE, is_verified=True
        )
        firmado = servicio.recordar_dispositivo(otro)
        self.assertFalse(servicio.dispositivo_recordado(self.usuario, firmado))


@override_settings(MFA_ENCRYPTION_KEY=LLAVE_MFA)
class CorreoComoSegundoFactorTests(BaseMfa):

    def test_el_ingreso_manda_el_codigo_y_lo_verifica(self):
        MfaUsuario.objects.create(
            usuario=self.usuario, metodo=METODO_CORREO, activo=True
        )
        self.cliente = APIClient()  # sin sesión: la API no tiene logout()

        with patch.object(servicio, "enviar_codigo") as enviar:
            r = self.cliente.post(reverse("token_obtain_pair"),
                                  {"email": "ana@empresa.co", "password": CLAVE},
                                  format="json")
        self.assertTrue(r.data["mfa_requerido"])
        enviar.assert_called_once()
        codigo = enviar.call_args[0][1]

        r = self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": r.data["mfa_token"], "codigo": codigo},
                              format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("access_token", r.cookies)

    def test_el_reenvio_no_reinicia_los_intentos(self):
        """Si los reiniciara, bastaría pedir otro correo para intentar sin fin."""
        MfaUsuario.objects.create(
            usuario=self.usuario, metodo=METODO_CORREO, activo=True
        )
        self.cliente = APIClient()  # sin sesión: la API no tiene logout()

        with patch.object(servicio, "enviar_codigo"):
            token = self.cliente.post(
                reverse("token_obtain_pair"),
                {"email": "ana@empresa.co", "password": CLAVE}, format="json",
            ).data["mfa_token"]
            self.cliente.post(reverse("token-mfa"),
                              {"mfa_token": token, "codigo": "000000"}, format="json")
            self.cliente.post(reverse("token-mfa-reenviar"), {"mfa_token": token},
                              format="json")

        self.assertEqual(MfaDesafio.objects.get().intentos, 1)
