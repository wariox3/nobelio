"""Pruebas de autenticación: API Key (ERP) y JWT (frontend)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory, APITestCase

from apps.catalogos.models import (
    Departamento,
    Municipio,
    Pais,
    TipoIdentificacion,
    TipoOrganizacion,
)
from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor
from apps.seguridad.autenticacion import LlaveApiAuthentication, PrincipalLlaveApi
from apps.seguridad.models import LlaveApi

Usuario = get_user_model()


def crear_emisor():
    """Crea un emisor mínimo (con su cuenta y catálogos) para las pruebas."""
    cuenta = Cuenta.objects.create(nombre="Cuenta de Prueba")
    tipo_id = TipoIdentificacion.objects.create(codigo="31", nombre="NIT")
    tipo_org = TipoOrganizacion.objects.create(codigo="1", nombre="Jurídica")
    pais = Pais.objects.create(codigo="CO", nombre="Colombia")
    depto = Departamento.objects.create(codigo="11", nombre="Bogotá D.C.")
    municipio = Municipio.objects.create(
        codigo="11001", nombre="Bogotá", departamento=depto
    )
    return Emisor.objects.create(
        cuenta=cuenta,
        razon_social="Empresa de Prueba S.A.S.",
        tipo_identificacion=tipo_id,
        numero_identificacion="900123456",
        tipo_organizacion=tipo_org,
        pais=pais,
        departamento=depto,
        municipio=municipio,
        direccion="Calle 1 # 2-3",
    )


class LlaveApiAuthenticationTests(APITestCase):
    """La clase de autenticación valida la cabecera Api-Key correctamente."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.auth = LlaveApiAuthentication()
        self.emisor = crear_emisor()
        self.llave, self.clave = LlaveApi.generar(
            emisor=self.emisor, nombre="ERP pruebas"
        )

    def _autenticar(self, credencial):
        request = self.factory.get(
            "/api/", HTTP_AUTHORIZATION=f"Api-Key {credencial}"
        )
        return self.auth.authenticate(request)

    def test_credencial_valida_devuelve_principal_con_emisor(self):
        usuario, llave = self._autenticar(self.clave)
        self.assertIsInstance(usuario, PrincipalLlaveApi)
        self.assertTrue(usuario.is_authenticated)
        self.assertEqual(usuario.emisor, self.emisor)
        self.assertEqual(llave, self.llave)

    def test_uso_registra_ultimo_uso(self):
        self.assertIsNone(self.llave.ultimo_uso_en)
        self._autenticar(self.clave)
        self.llave.refresh_from_db()
        self.assertIsNotNone(self.llave.ultimo_uso_en)

    def test_secreto_no_se_guarda_en_claro(self):
        self.assertNotIn(self.clave.split(".")[1], self.llave.clave_hash)

    def test_secreto_incorrecto_falla(self):
        prefijo = self.clave.split(".")[0]
        with self.assertRaises(AuthenticationFailed):
            self._autenticar(f"{prefijo}.secretoequivocado")

    def test_prefijo_inexistente_falla(self):
        with self.assertRaises(AuthenticationFailed):
            self._autenticar("noexiste.loquesea")

    def test_llave_inactiva_falla(self):
        self.llave.activa = False
        self.llave.save(update_fields=["activa"])
        with self.assertRaises(AuthenticationFailed):
            self._autenticar(self.clave)

    def test_llave_expirada_falla(self):
        self.llave.expira_en = timezone.now() - timedelta(seconds=1)
        self.llave.save(update_fields=["expira_en"])
        with self.assertRaises(AuthenticationFailed):
            self._autenticar(self.clave)

    def test_otra_cabecera_se_ignora(self):
        # Un Bearer (JWT) no es asunto de esta clase: devuelve None.
        request = self.factory.get("/api/", HTTP_AUTHORIZATION="Bearer xyz")
        self.assertIsNone(self.auth.authenticate(request))


class LlaveApiEndToEndTests(APITestCase):
    """El ERP accede a la API real usando solo la cabecera Api-Key."""

    def setUp(self):
        self.emisor = crear_emisor()
        _, self.clave = LlaveApi.generar(emisor=self.emisor, nombre="ERP")

    def test_acceso_a_endpoint_autenticado_con_api_key(self):
        # /api/emisores/ exige autenticación; la API Key debe bastar.
        resp = self.client.get(
            "/api/emisores/", HTTP_AUTHORIZATION=f"Api-Key {self.clave}"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_sin_credencial_no_autorizado(self):
        resp = self.client.get("/api/emisores/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class JWTLoginTests(APITestCase):
    """El frontend obtiene tokens con email + contraseña y los usa."""

    URL_TOKEN = "/api/seguridad/token/"
    URL_REFRESH = "/api/seguridad/token/refresh/"

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="frontend@example.com", password="ClaveSegura123"
        )

    def test_login_devuelve_access_y_refresh(self):
        resp = self.client.post(
            self.URL_TOKEN,
            {"email": "frontend@example.com", "password": "ClaveSegura123"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_token_da_acceso_a_la_api(self):
        token = self.client.post(
            self.URL_TOKEN,
            {"email": "frontend@example.com", "password": "ClaveSegura123"},
        ).data["access"]
        resp = self.client.get(
            "/api/emisores/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_refresh_renueva_el_access(self):
        refresh = self.client.post(
            self.URL_TOKEN,
            {"email": "frontend@example.com", "password": "ClaveSegura123"},
        ).data["refresh"]
        resp = self.client.post(self.URL_REFRESH, {"refresh": refresh})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)

    def test_credenciales_invalidas_rechazadas(self):
        resp = self.client.post(
            self.URL_TOKEN,
            {"email": "frontend@example.com", "password": "claveerronea"},
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
