"""La cuenta: un agrupador opcional de emisores (p. ej. el ERP que los integra)."""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Cuenta, Emisor
from apps.emisores.views.cuenta import MENSAJE_CUENTA_CON_EMISORES
from apps.seguridad.models import Usuario

URL_CUENTAS = "/api/emisores/cuenta/"
URL_EMISORES = "/api/emisores/emisor/"


class CuentaTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(self.admin)

    def crear_emisor(self, cuenta=None):
        c = self.cat
        return Emisor.objects.create(
            usuario=self.admin,
            cuenta=cuenta,
            razon_social="Semantica Digital S.A.S",
            tipo_identificacion=c["nit"],
            numero_identificacion="901192048",
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def payload_emisor(self, **extra):
        c = self.cat
        datos = {
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": c["nit"].id,
            "numero_identificacion": "901192048",
            "tipo_organizacion": c["juridica"].id,
            "pais": c["colombia"].id,
            "departamento": c["antioquia"].id,
            "municipio": c["medellin"].id,
            "direccion": "Calle 1 # 2-3",
            "correo": "facturacion@empresa.co",
        }
        datos.update(extra)
        return datos

    # --- CRUD ----------------------------------------------------------------

    def test_crea_y_devuelve_id_y_nombre(self):
        resp = self.client.post(URL_CUENTAS, {"nombre": "RedDoc ERP"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(set(resp.data), {"id", "nombre"})
        self.assertEqual(resp.data["nombre"], "RedDoc ERP")

    def test_el_nombre_no_se_repite(self):
        Cuenta.objects.create(nombre="RedDoc ERP")
        resp = self.client.post(URL_CUENTAS, {"nombre": "RedDoc ERP"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_solo_staff(self):
        self.client.force_authenticate(crear_usuario(nombre="Cliente"))
        self.assertEqual(
            self.client.get(URL_CUENTAS).status_code, status.HTTP_403_FORBIDDEN
        )
        resp = self.client.post(URL_CUENTAS, {"nombre": "Semantica ERP"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_borra_una_cuenta_vacia(self):
        cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        resp = self.client.delete(f"{URL_CUENTAS}{cuenta.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cuenta.objects.exists())

    def test_no_borra_una_cuenta_con_emisores(self):
        cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        self.crear_emisor(cuenta=cuenta)
        resp = self.client.delete(f"{URL_CUENTAS}{cuenta.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["detail"], MENSAJE_CUENTA_CON_EMISORES)
        self.assertTrue(Cuenta.objects.filter(pk=cuenta.pk).exists())

    # --- En el emisor ---------------------------------------------------------

    def test_el_emisor_nace_sin_cuenta(self):
        resp = self.client.post(URL_EMISORES, self.payload_emisor(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data["cuenta"])

    def test_el_emisor_se_da_de_alta_en_una_cuenta(self):
        cuenta = Cuenta.objects.create(nombre="Semantica ERP")
        resp = self.client.post(
            URL_EMISORES, self.payload_emisor(cuenta=cuenta.id), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["cuenta"], cuenta.id)

    def test_se_cambia_y_se_quita_la_cuenta_del_emisor(self):
        emisor = self.crear_emisor()
        cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        url = f"{URL_EMISORES}{emisor.id}/"

        resp = self.client.patch(url, {"cuenta": cuenta.id}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        emisor.refresh_from_db()
        self.assertEqual(emisor.cuenta, cuenta)

        resp = self.client.patch(url, {"cuenta": None}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        emisor.refresh_from_db()
        self.assertIsNone(emisor.cuenta)

    def test_una_cuenta_inexistente_se_rechaza(self):
        resp = self.client.post(
            URL_EMISORES, self.payload_emisor(cuenta=999999), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
