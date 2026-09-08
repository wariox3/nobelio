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
        resp = self.client.post(
            self.URL, {"nombre": "Cliente Uno", "usuario": self.admin.pk}
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Cuenta.objects.filter(nombre="Cliente Uno").exists())

    def test_no_autenticado_rechazado(self):
        resp = self.client.post(self.URL, {"nombre": "X"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_usuario_sin_cuentas_lista_vacio(self):
        """Ya no es un 403: se puede listar, pero no hay nada que ver."""
        normal = Usuario.objects.create_user(
            email="normal@example.com", password="Clave12345"
        )
        self.client.force_authenticate(normal)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)


class CuentaAislamientoTests(APITestCase):
    """Cada usuario solo alcanza las cuentas de las que es dueño."""

    URL = "/api/cuentas/cuenta/"

    def setUp(self):
        self.ana = Usuario.objects.create_user(
            email="ana@example.com", password="Clave12345"
        )
        self.beto = Usuario.objects.create_user(
            email="beto@example.com", password="Clave12345"
        )
        self.de_ana = Cuenta.objects.create(nombre="Cuenta de Ana", usuario=self.ana)
        self.de_beto = Cuenta.objects.create(nombre="Cuenta de Beto", usuario=self.beto)

    def test_solo_lista_las_propias(self):
        self.client.force_authenticate(self.ana)
        resp = self.client.get(self.URL)
        nombres = [c["nombre"] for c in resp.data["results"]]
        self.assertEqual(nombres, ["Cuenta de Ana"])

    def test_la_ajena_no_existe(self):
        """404 y no 403: el endpoint no sirve para descubrir qué ids hay."""
        self.client.force_authenticate(self.ana)
        resp = self.client.get(f"{self.URL}{self.de_beto.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_puede_eliminar_la_ajena(self):
        self.client.force_authenticate(self.ana)
        resp = self.client.delete(f"{self.URL}{self.de_beto.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Cuenta.objects.filter(pk=self.de_beto.pk).exists())

    def test_no_puede_modificar_la_ajena(self):
        self.client.force_authenticate(self.ana)
        resp = self.client.patch(
            f"{self.URL}{self.de_beto.pk}/", {"nombre": "Mía ahora"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_puede_eliminar_la_suya(self):
        self.client.force_authenticate(self.ana)
        resp = self.client.delete(f"{self.URL}{self.de_ana.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cuenta.objects.filter(pk=self.de_ana.pk).exists())

    def test_la_crea_a_su_nombre_aunque_pida_otro(self):
        """Mandar `usuario` en el cuerpo no abre una cuenta a nombre ajeno."""
        self.client.force_authenticate(self.ana)
        resp = self.client.post(
            self.URL, {"nombre": "Nueva", "usuario": self.beto.pk}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Cuenta.objects.get(nombre="Nueva").usuario, self.ana)

    def test_no_puede_borrar_una_cuenta_con_emisores(self):
        """`Emisor.cuenta` es PROTECT: sin manejarlo esto era un 500."""
        from apps.documentos.tests_utils import crear_catalogos_minimos
        from apps.emisores.models import Emisor

        cat = crear_catalogos_minimos()
        Emisor.objects.create(
            cuenta=self.de_ana,
            razon_social="Empresa de Ana SAS",
            tipo_identificacion=cat["nit"],
            numero_identificacion="900123456",
            digito_verificacion="1",
            tipo_organizacion=cat["juridica"],
            pais=cat["colombia"],
            departamento=cat["antioquia"],
            municipio=cat["medellin"],
            direccion="Calle 1 # 2-3",
            correo="facturacion@empresa.co",
        )
        self.client.force_authenticate(self.ana)
        resp = self.client.delete(f"{self.URL}{self.de_ana.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("emisores", resp.data["detail"])
        self.assertTrue(Cuenta.objects.filter(pk=self.de_ana.pk).exists())

    def test_no_puede_regalar_su_cuenta(self):
        self.client.force_authenticate(self.ana)
        self.client.patch(
            f"{self.URL}{self.de_ana.pk}/", {"usuario": self.beto.pk}, format="json"
        )
        self.de_ana.refresh_from_db()
        self.assertEqual(self.de_ana.usuario, self.ana)
