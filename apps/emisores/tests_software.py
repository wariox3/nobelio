"""Pruebas de la API de software DIAN (PIN write-only, filtro por emisor)."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.emisores.models import Emisor, SoftwareDian


def _crear_emisor(cat, nit="901192048"):
    emisor = Emisor.objects.create(
        cuenta=cat["cuenta"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )
    # El certificado va antes que el software: sin él no se puede registrar.
    crear_certificado(emisor)
    return emisor


class SoftwareDianAPITests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.usuario)
        self.url = "/api/emisores/software/"

    def _payload(self):
        return {
            "emisor": self.emisor.id,
            "tipo": SoftwareDian.Tipo.FACTURACION,
            "identificador": "abc123-software-id",
            "pin": "12345",
            "test_set_id": "set-pruebas-xyz",
        }

    def test_crea_software_y_devuelve_el_pin(self):
        # El PIN se devuelve a propósito (decisión del 2026-08-28, reafirmada el
        # 2026-09-02): quien puede leer el software ya está dentro del alcance
        # del emisor, y tenerlo a la vista ahorra fricción en la habilitación.
        # Esta prueba existía afirmando lo contrario, de cuando era write_only.
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["pin"], "12345")
        creado = SoftwareDian.objects.get(pk=resp.data["id"])
        self.assertEqual(creado.pin, "12345")

    def test_el_tipo_es_obligatorio(self):
        # Sin tipo no se sabe qué operación habilita el software, y el pipeline
        # busca el activo *de su tipo*: uno sin tipo no lo encontraría nunca.
        payload = self._payload()
        del payload["tipo"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tipo", resp.data["errores"])

    def test_pin_es_obligatorio(self):
        payload = self._payload()
        del payload["pin"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("pin", resp.data["errores"])

    def test_filtra_por_emisor(self):
        SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="s1", pin="1"
        )
        otro = _crear_emisor(self.cat, nit="800197268")
        SoftwareDian.objects.create(
            emisor=otro, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="s2", pin="2"
        )
        resp = self.client.get(self.url, {"emisor": self.emisor.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    def test_requiere_autenticacion(self):
        from rest_framework.test import APIClient
        resp = APIClient().post(self.url, self._payload(), format="json")
        self.assertIn(resp.status_code, (401, 403))
