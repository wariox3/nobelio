"""Pruebas del alta pública: registro, verificación y lo que desbloquea."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_cuenta
from apps.seguridad import verificacion

Usuario = get_user_model()

CUERPO = {
    "email": "ana@empresa.co",
    "password": "una-clave-larga-y-decente",
    "nombre_corto": "Ana",
}


class RegistroTests(TestCase):
    """El alta crea usuario y cuenta, y deja al usuario como dueño."""

    def setUp(self):
        self.cliente = APIClient()
        self.url = reverse("registro")

    def _registrar(self, **cambios):
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            return self.cliente.post(self.url, {**CUERPO, **cambios}, format="json")

    def test_crea_el_usuario(self):
        r = self._registrar()
        self.assertEqual(r.status_code, 201)
        self.assertTrue(Usuario.objects.filter(email="ana@empresa.co").exists())

    def test_sin_nombre_corto_usa_lo_de_antes_de_la_arroba(self):
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            cuerpo = {k: v for k, v in CUERPO.items() if k != "nombre_corto"}
            self.cliente.post(self.url, cuerpo, format="json")
        usuario = Usuario.objects.get(email="ana@empresa.co")
        self.assertEqual(usuario.nombre_corto, "ana")

    def test_el_nombre_corto_que_se_manda_se_respeta(self):
        self._registrar(nombre_corto="Anita")
        usuario = Usuario.objects.get(email="ana@empresa.co")
        self.assertEqual(usuario.nombre_corto, "Anita")

    def test_no_crea_ninguna_cuenta(self):
        """El alta es solo del usuario; la cuenta se crea después, autenticado."""
        self._registrar()
        self.assertEqual(Cuenta.objects.count(), 0)

    def test_nace_sin_verificar_y_sin_privilegios(self):
        self._registrar()
        usuario = Usuario.objects.get(email="ana@empresa.co")
        self.assertFalse(usuario.is_verified)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_el_cuerpo_no_puede_colar_privilegios(self):
        """`is_staff` y compañía no entran aunque vengan en el JSON."""
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            r = self.cliente.post(
                self.url,
                {**CUERPO, "is_staff": True, "is_superuser": True,
                 "is_verified": True},
                format="json",
            )
        self.assertEqual(r.status_code, 201)
        usuario = Usuario.objects.get(email="ana@empresa.co")
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertFalse(usuario.is_verified)

    def test_correo_repetido_se_rechaza(self):
        self._registrar()
        r = self._registrar()
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data["errores"])

    def test_contrasena_debil_se_rechaza(self):
        r = self._registrar(password="123")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.data["errores"])
        self.assertFalse(Usuario.objects.filter(email="ana@empresa.co").exists())

    def test_si_el_correo_no_sale_la_cuenta_igual_queda(self):
        """La pasarela caída no puede costar el alta; se avisa y se reenvía."""
        with patch.object(verificacion, "enviar_verificacion", return_value=False):
            r = self.cliente.post(self.url, CUERPO, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.data["correo_enviado"])
        self.assertTrue(Usuario.objects.filter(email="ana@empresa.co").exists())


class VerificacionTests(TestCase):
    """El token confirma el correo, y solo el correcto."""

    def setUp(self):
        self.cliente = APIClient()
        self.url = reverse("registro-verificar")
        self.usuario = Usuario.objects.create_user(
            email="ana@empresa.co", password="una-clave-larga-y-decente"
        )

    def test_token_valido_verifica(self):
        token = verificacion.generar_token(self.usuario)
        r = self.cliente.post(self.url, {"token": token}, format="json")
        self.assertEqual(r.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_verified)

    def test_es_idempotente(self):
        token = verificacion.generar_token(self.usuario)
        self.cliente.post(self.url, {"token": token}, format="json")
        r = self.cliente.post(self.url, {"token": token}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_token_manipulado_se_rechaza(self):
        r = self.cliente.post(self.url, {"token": "no-es-un-token"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.is_verified)

    def test_token_caducado_se_rechaza(self):
        token = verificacion.generar_token(self.usuario)
        with patch.object(verificacion, "VIGENCIA_SEGUNDOS", -1):
            r = self.cliente.post(self.url, {"token": token}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("caducó", r.data["detail"])

    def test_cambiar_el_correo_invalida_el_token(self):
        token = verificacion.generar_token(self.usuario)
        self.usuario.email = "otra@empresa.co"
        self.usuario.save(update_fields=["email"])
        r = self.cliente.post(self.url, {"token": token}, format="json")
        self.assertEqual(r.status_code, 400)


class LoginTests(TestCase):
    """Sin correo confirmado no se entra, aunque la contraseña sea correcta."""

    def setUp(self):
        self.cliente = APIClient()
        self.url = reverse("token_obtain_pair")
        self.credenciales = {
            "email": "ana@empresa.co",
            "password": "una-clave-larga-y-decente",
        }
        self.usuario = Usuario.objects.create_user(**self.credenciales)

    def test_sin_verificar_no_entra(self):
        r = self.cliente.post(self.url, self.credenciales, format="json")
        self.assertEqual(r.status_code, 400)

    def test_verificado_entra(self):
        self.usuario.is_verified = True
        self.usuario.save(update_fields=["is_verified"])
        r = self.cliente.post(self.url, self.credenciales, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)


class ReenvioTests(TestCase):
    """El reenvío no revela quién tiene cuenta."""

    def setUp(self):
        self.cliente = APIClient()
        self.url = reverse("registro-reenviar")

    def test_responde_igual_exista_o_no(self):
        Usuario.objects.create_user(
            email="ana@empresa.co", password="una-clave-larga-y-decente"
        )
        with patch.object(verificacion, "enviar_verificacion", return_value=True):
            existe = self.cliente.post(
                self.url, {"email": "ana@empresa.co"}, format="json"
            )
            no_existe = self.cliente.post(
                self.url, {"email": "nadie@empresa.co"}, format="json"
            )
        self.assertEqual(existe.status_code, no_existe.status_code)
        self.assertEqual(existe.data, no_existe.data)


class AlcanceDelDuenoTests(TestCase):
    """Ser dueño de una cuenta es lo que desbloquea dar de alta emisores."""

    def setUp(self):
        self.cliente = APIClient()
        self.cat = crear_catalogos_minimos()
        self.usuario = Usuario.objects.create_user(
            email="ana@empresa.co",
            password="una-clave-larga-y-decente",
            is_verified=True,
        )

    def _alta_emisor(self):
        return self.cliente.post(
            reverse("emisor-list"),
            {
                "razon_social": "Empresa de Ana SAS",
                "tipo_identificacion": self.cat["nit"].pk,
                "numero_identificacion": "900123456",
                "digito_verificacion": "1",
                "tipo_organizacion": self.cat["juridica"].pk,
                "responsabilidades": [],
                "pais": "CO",
                "departamento": "05",
                "municipio": "05001",
                "direccion": "Calle 1 # 2-3",
                "correo": "facturacion@empresa.co",
            },
            format="json",
        )

    def test_sin_cuenta_propia_no_da_de_alta(self):
        self.cliente.force_authenticate(user=self.usuario)
        self.assertEqual(self._alta_emisor().status_code, 403)

    def test_el_dueno_da_de_alta_en_su_cuenta(self):
        cuenta = crear_cuenta("Empresa de Ana SAS", usuario=self.usuario)
        self.cliente.force_authenticate(user=self.usuario)
        r = self._alta_emisor()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["cuenta"], cuenta.pk)

    def test_el_dueno_alcanza_los_emisores_de_su_cuenta(self):
        """Sin asignárselos: los alcanza por ser dueño de la cuenta."""
        crear_cuenta("Empresa de Ana SAS", usuario=self.usuario)
        self.cliente.force_authenticate(user=self.usuario)
        self._alta_emisor()
        r = self.cliente.get(reverse("emisor-list"))
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(self.usuario.emisores.count(), 0)
