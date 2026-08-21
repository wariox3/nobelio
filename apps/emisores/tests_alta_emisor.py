"""Reglas del alta del emisor: el NIT no se contrasta con el RUES.

El RUES es un registro ajeno y a veces caído: consultarlo al dar de alta ataba
la creación de emisores a que un tercero respondiera. Queda como consulta
voluntaria en ``GET /api/emisores/emisor/validar-nit/``, para autocompletar el
formulario. Lo que sí se sigue exigiendo es que el emisor no esté ya dado de
alta en esa cuenta (ver ``tests_multicuenta``).
"""
from unittest import mock

from rest_framework import status
from rest_framework.test import APITestCase

from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor
from apps.seguridad.models import Usuario

URL_EMISORES = "/api/emisores/emisor/"
_RUES = "apps.utilidades.rues.consultar_nit"


class AltaSinValidarRuesTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

    def payload(self, **extra):
        c = self.cat
        datos = {
            "cuenta": self.cuenta.id,
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": c["nit"].id,
            "numero_identificacion": "901192048",
            "tipo_organizacion": c["juridica"].id,
            "pais": c["colombia"].codigo,
            "departamento": c["antioquia"].codigo,
            "municipio": c["medellin"].codigo,
            "direccion": "Calle 1 # 2-3",
        }
        datos.update(extra)
        return datos

    def test_el_alta_no_consulta_el_rues(self):
        with mock.patch(_RUES) as consultar:
            resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        consultar.assert_not_called()

    def test_un_nit_que_no_esta_en_el_rues_no_bloquea_el_alta(self):
        with mock.patch(_RUES, return_value=None):
            resp = self.client.post(
                URL_EMISORES, self.payload(numero_identificacion="000000000"),
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertTrue(
            Emisor.objects.filter(numero_identificacion="000000000").exists()
        )

    # --- Ubicación por código, no por id ----------------------------------

    def test_la_ubicacion_llega_por_codigo_y_se_guarda_la_fila_correcta(self):
        resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        emisor = Emisor.objects.get(numero_identificacion="901192048")
        self.assertEqual(emisor.pais, self.cat["colombia"])
        self.assertEqual(emisor.departamento, self.cat["antioquia"])
        self.assertEqual(emisor.municipio, self.cat["medellin"])

    def test_la_respuesta_devuelve_los_codigos(self):
        resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.data["pais"], "CO")
        self.assertEqual(resp.data["departamento"], "05")
        self.assertEqual(resp.data["municipio"], "05001")

    def test_un_codigo_que_no_esta_en_el_catalogo_se_rechaza(self):
        resp = self.client.post(
            URL_EMISORES, self.payload(municipio="99999"), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("municipio", resp.data["errores"])

    def test_mandar_el_id_en_vez_del_codigo_se_rechaza(self):
        resp = self.client.post(
            URL_EMISORES,
            self.payload(municipio=self.cat["medellin"].id),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("municipio", resp.data["errores"])

    def test_editar_el_nit_tampoco_consulta_el_rues(self):
        self.client.post(URL_EMISORES, self.payload(), format="json")
        emisor = Emisor.objects.get(numero_identificacion="901192048")
        with mock.patch(_RUES) as consultar:
            resp = self.client.patch(
                f"{URL_EMISORES}{emisor.id}/",
                {"numero_identificacion": "900123456"},
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        consultar.assert_not_called()
