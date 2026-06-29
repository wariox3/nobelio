"""Pruebas de la API de usuarios (creación restringida a staff/admin)."""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

Usuario = get_user_model()


class UsuarioAPITests(APITestCase):
    URL = "/api/seguridad/usuario/"

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email="admin@example.com", password="ClaveSegura123"
        )

    def test_staff_puede_crear_usuario(self):
        self.client.force_authenticate(self.admin)
        datos = {
            "email": "nuevo@example.com",
            "nombres": "Nuevo",
            "password": "OtraClave456",
        }
        resp = self.client.post(self.URL, datos)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # La contraseña no se devuelve y queda hasheada (no en texto plano).
        self.assertNotIn("password", resp.data)
        creado = Usuario.objects.get(email="nuevo@example.com")
        self.assertNotEqual(creado.password, "OtraClave456")
        self.assertTrue(creado.check_password("OtraClave456"))

    def test_no_autenticado_no_puede_crear(self):
        # Sin credenciales -> 401 (las clases de auth envían WWW-Authenticate).
        resp = self.client.post(self.URL, {"email": "x@x.com", "password": "Clave12345"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_usuario_no_staff_no_puede_listar(self):
        normal = Usuario.objects.create_user(
            email="normal@example.com", password="Clave12345"
        )
        self.client.force_authenticate(normal)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_password_obligatoria_al_crear(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(self.URL, {"email": "sinclave@example.com"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", resp.data["errores"])
