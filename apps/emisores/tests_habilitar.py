"""Pruebas de la habilitación del emisor: ``habilitar/`` y ``set-pruebas/``."""
from unittest import mock

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.dian.servicios import ErrorEmision
from apps.emisores.models import Emisor, SoftwareDian
from apps.seguridad.models import Usuario

URL = "/api/emisores/emisor/habilitar/"


class HabilitarEmisorTests(APITestCase):
    SET_PRUEBAS = {
        "factura": {"numero": "SETP990000000", "cufe_cude": "a" * 96,
                    "track_id": "zipkey-1", "es_valido": True,
                    "codigo_estado": "00", "descripcion_estado": "ok", "errores": []},
        "nota_credito": {"numero": "SETP990000001", "cufe_cude": "b" * 96,
                         "track_id": "zipkey-2", "es_valido": True,
                         "codigo_estado": "00", "descripcion_estado": "ok",
                         "errores": []},
    }

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        self.otra_cuenta = Cuenta.objects.create(nombre="ERP XYZ")
        self.emisor = self.crear_emisor(self.cuenta, "901192048")
        self.ajeno = self.crear_emisor(self.otra_cuenta, "900123456")

        self.admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.usuario = Usuario.objects.create_user(
            email="humano@nobelio.co", password="ClaveSegura123"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.admin)

        # El Set de Pruebas sale a la red contra la DIAN; aquí se dobla. El
        # pipeline lo cubre `apps.dian.tests_set_pruebas`.
        parche = mock.patch(
            "apps.emisores.views.emisor.dian.emitir_set_pruebas",
            return_value=self.SET_PRUEBAS,
        )
        self.emitir = parche.start()
        self.addCleanup(parche.stop)

    def crear_emisor(self, cuenta, nit):
        c = self.cat
        emisor = Emisor.objects.create(
            cuenta=cuenta, razon_social="Semantica Digital S.A.S",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )
        # El certificado va antes que el software en el flujo.
        crear_certificado(emisor)
        return emisor

    def payload(self, **extra):
        datos = {
            "emisor": self.emisor.id,
            "identificador": "94966156-8084-428b-b1b1-a903a053aed1",
            "pin": "12345",
            "test_set_id": "0d26ba8c-8584-4199-b210-2ddc063c3ddd",
        }
        datos.update(extra)
        return datos

    def test_crea_el_software_del_emisor(self):
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        software = SoftwareDian.objects.get(emisor=self.emisor)
        self.assertEqual(software.identificador, "94966156-8084-428b-b1b1-a903a053aed1")
        self.assertEqual(software.pin, "12345")
        self.assertEqual(software.test_set_id, "0d26ba8c-8584-4199-b210-2ddc063c3ddd")
        self.assertTrue(software.activo)
        self.assertFalse(software.set_pruebas_aceptado)

    def test_el_pin_no_vuelve_en_la_respuesta(self):
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertNotIn("pin", resp.data["software"])

    # --- El Set de Pruebas va en la misma llamada -------------------------

    def test_emite_el_set_de_pruebas_y_devuelve_su_resultado(self):
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["set_pruebas"], self.SET_PRUEBAS)
        self.assertEqual(self.emitir.call_args.args[0], self.emisor)
        self.assertIsNone(self.emitir.call_args.kwargs["consecutivo"])

    def test_pasa_el_consecutivo_indicado(self):
        self.client.post(URL, self.payload(consecutivo=990000500), format="json")
        self.assertEqual(self.emitir.call_args.kwargs["consecutivo"], 990000500)

    def test_si_falla_el_envio_el_software_queda_registrado(self):
        self.emitir.side_effect = ErrorEmision("La DIAN no responde.")
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["set_pruebas"], {"error": "La DIAN no responde."})
        self.assertTrue(SoftwareDian.objects.filter(emisor=self.emisor).exists())

    def test_no_se_emite_por_un_emisor_fuera_del_alcance(self):
        self.client.force_authenticate(self.usuario)
        resp = self.client.post(URL, self.payload(emisor=self.ajeno.id), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.emitir.assert_not_called()

    # --- El Set de Pruebas va en la misma llamada -------------------------

    def test_emite_el_set_de_pruebas_y_devuelve_su_resultado(self):
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["set_pruebas"], self.SET_PRUEBAS)
        self.assertEqual(self.emitir.call_args.args[0], self.emisor)
        self.assertIsNone(self.emitir.call_args.kwargs["consecutivo"])

    def test_pasa_el_consecutivo_indicado(self):
        self.client.post(URL, self.payload(consecutivo=990000500), format="json")
        self.assertEqual(self.emitir.call_args.kwargs["consecutivo"], 990000500)

    def test_si_falla_el_envio_el_software_queda_registrado(self):
        self.emitir.side_effect = ErrorEmision("La DIAN no responde.")
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["set_pruebas"], {"error": "La DIAN no responde."})
        self.assertTrue(SoftwareDian.objects.filter(emisor=self.emisor).exists())

    def test_no_se_emite_por_un_emisor_fuera_del_alcance(self):
        self.client.force_authenticate(self.usuario)
        resp = self.client.post(URL, self.payload(emisor=self.ajeno.id), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.emitir.assert_not_called()

    def test_sin_identificador_responde_400(self):
        resp = self.client.post(URL, self.payload(identificador=""), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("identificador", resp.data["errores"])

    def test_no_se_puede_habilitar_un_emisor_fuera_del_alcance(self):
        self.client.force_authenticate(self.usuario)
        resp = self.client.post(URL, self.payload(emisor=self.ajeno.id), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(SoftwareDian.objects.filter(emisor=self.ajeno).exists())

    def test_exige_autenticacion(self):
        # Cliente propio: `force_authenticate(None)` sobre el de la clase toca
        # la sesión, y el proyecto no tiene `django.contrib.sessions` instalada.
        resp = APIClient().post(URL, self.payload(), format="json")
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
