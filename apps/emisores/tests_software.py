"""Pruebas de la API de software DIAN (PIN write-only, filtro por emisor)."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor, SoftwareDian


def _crear_emisor(cat, nit="901192048"):
    return Emisor.objects.create(
        cuenta=cat["cuenta"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )


class SoftwareDianAPITests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.client.force_authenticate(self.usuario)
        self.url = "/api/emisores/software/"

    def _payload(self):
        return {
            "emisor": self.emisor.id,
            "identificador": "abc123-software-id",
            "pin": "12345",
            "id_proveedor": "901192048",
            "test_set_id": "set-pruebas-xyz",
        }

    def test_crea_software_y_no_devuelve_pin(self):
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertNotIn("pin", resp.data)  # write-only: nunca se expone
        creado = SoftwareDian.objects.get(pk=resp.data["id"])
        self.assertEqual(creado.pin, "12345")  # sí se guardó

    def test_pin_es_obligatorio(self):
        payload = self._payload()
        del payload["pin"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("pin", resp.data)

    def test_filtra_por_emisor(self):
        SoftwareDian.objects.create(
            emisor=self.emisor, identificador="s1", pin="1", id_proveedor="901192048"
        )
        otro = _crear_emisor(self.cat, nit="800197268")
        SoftwareDian.objects.create(
            emisor=otro, identificador="s2", pin="2", id_proveedor="800197268"
        )
        resp = self.client.get(self.url, {"emisor": self.emisor.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    def test_requiere_autenticacion(self):
        from rest_framework.test import APIClient
        resp = APIClient().post(self.url, self._payload(), format="json")
        self.assertIn(resp.status_code, (401, 403))
