"""Pruebas de la API de cuentas (gestión restringida a staff)."""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cuentas.models import Cuenta

Usuario = get_user_model()


class CuentaAPITests(APITestCase):
    URL = "/api/cuentas/cuenta/"

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email="admin@example.com", password="ClaveSegura123"
        )

    def test_staff_puede_crear_cuenta(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(self.URL, {"nombre": "Cliente Uno"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Cuenta.objects.filter(nombre="Cliente Uno").exists())

    def test_no_autenticado_rechazado(self):
        resp = self.client.post(self.URL, {"nombre": "X"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_usuario_no_staff_no_puede_listar(self):
        normal = Usuario.objects.create_user(
            email="normal@example.com", password="Clave12345"
        )
        self.client.force_authenticate(normal)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
