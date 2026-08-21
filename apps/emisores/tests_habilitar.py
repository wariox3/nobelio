"""Pruebas de ``crear-habilitacion/``: la habilitación entera en una llamada."""
from unittest import mock

import requests

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.dian.servicios import ErrorEmision
from apps.documentos.models import Documento
from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian
from apps.seguridad.models import Usuario

URL = "/api/emisores/emisor/crear-habilitacion/"


class FixtureHabilitacion:
    """Emisor con certificado, su cuenta, uno ajeno y los usuarios de cada uno.

    Vive aparte porque lo comparten ``crear-habilitacion/`` y
    ``validar-habilitacion/``; heredar de la otra clase de pruebas repetiría
    todos sus casos.
    """

    def montar_fixture(self):
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


class HabilitarEmisorTests(FixtureHabilitacion, APITestCase):
    # Lo único que devuelve emitir_set_pruebas son los ids de las dos filas.
    SET_PRUEBAS = {
        "factura": "3f2b1c4e-0000-4000-8000-000000000001",
        "nota_credito": "3f2b1c4e-0000-4000-8000-000000000002",
    }

    def setUp(self):
        self.montar_fixture()

        # El Set de Pruebas sale a la red contra la DIAN; aquí se dobla. El
        # pipeline lo cubre `apps.dian.tests_set_pruebas`.
        parche = mock.patch(
            "apps.emisores.views.emisor.dian.emitir_set_pruebas",
            return_value=self.SET_PRUEBAS,
        )
        self.emitir = parche.start()
        self.addCleanup(parche.stop)

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

    def test_la_respuesta_son_solo_los_ids(self):
        resp = self.client.post(URL, self.payload(), format="json")
        # Ni el software ni el pin: el resto se consulta por el id.
        self.assertEqual(resp.data, self.SET_PRUEBAS)

    # --- El Set de Pruebas va en la misma llamada -------------------------

    def test_emite_el_set_de_pruebas_y_devuelve_los_ids(self):
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data, self.SET_PRUEBAS)
        self.assertEqual(self.emitir.call_args.args[0], self.emisor)
        self.assertIsNone(self.emitir.call_args.kwargs["consecutivo"])

    def test_no_marca_al_emisor_como_habilitado(self):
        """Enviar no es estar habilitado: eso lo decide validar-habilitacion."""
        self.client.post(URL, self.payload(), format="json")
        self.emisor.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_facturacion)

    def test_pasa_el_consecutivo_indicado(self):
        self.client.post(URL, self.payload(consecutivo=990000500), format="json")
        self.assertEqual(self.emitir.call_args.kwargs["consecutivo"], 990000500)

    def test_si_falla_el_envio_no_queda_registrado_nada(self):
        """Todo o nada: media habilitación deja el identificador tomado."""
        previo = SoftwareDian.objects.create(
            emisor=self.emisor, identificador="anterior", pin="1",
            test_set_id="x", activo=True,
        )
        self.emitir.side_effect = ErrorEmision("La DIAN no responde.")

        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(resp.data["detail"], "La DIAN no responde.")
        # Ni software nuevo, ni resolución, ni el anterior jubilado.
        self.assertEqual(SoftwareDian.objects.count(), 1)
        previo.refresh_from_db()
        self.assertTrue(previo.activo)
        self.assertFalse(ResolucionFacturacion.objects.exists())
        self.assertFalse(Documento.objects.exists())

    def test_si_la_dian_rechaza_no_queda_registrado_nada(self):
        """Un rechazo del Set de Pruebas deshace la habilitación entera."""
        self.emitir.side_effect = ErrorEmision(
            "La DIAN rechazó Factura de venta SETP990000000 del Set de Pruebas: "
            "CBG04a: Documento referenciado no existe."
        )

        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("CBG04a", resp.data["detail"])
        self.assertFalse(SoftwareDian.objects.exists())
        self.assertFalse(ResolucionFacturacion.objects.exists())
        self.assertFalse(Documento.objects.exists())
        self.emisor.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_facturacion)

    def test_si_la_dian_no_responde_da_502_y_no_registra_nada(self):
        self.emitir.side_effect = requests.ConnectionError("sin ruta al host")

        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY, resp.data)
        self.assertFalse(SoftwareDian.objects.exists())
        self.assertFalse(Documento.objects.exists())

    def test_se_puede_reintentar_con_los_mismos_datos_tras_un_fallo(self):
        """Como no quedó nada, el identificador sigue libre."""
        self.emitir.side_effect = requests.ConnectionError("sin ruta al host")
        self.client.post(URL, self.payload(), format="json")

        self.emitir.side_effect = None
        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_no_se_emite_por_un_emisor_fuera_del_alcance(self):
        self.client.force_authenticate(self.usuario)
        resp = self.client.post(URL, self.payload(emisor=self.ajeno.id), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.emitir.assert_not_called()

    # --- El SoftwareID se registra una sola vez ---------------------------

    def test_un_identificador_ya_registrado_se_rechaza(self):
        self.client.post(URL, self.payload(), format="json")
        self.emitir.reset_mock()

        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("ya está registrado", resp.data["errores"]["identificador"][0])
        # Ni se creó un segundo registro ni se volvió a emitir.
        self.assertEqual(SoftwareDian.objects.count(), 1)
        self.emitir.assert_not_called()

    def test_un_identificador_de_otro_emisor_se_rechaza_sin_delatarlo(self):
        SoftwareDian.objects.create(
            emisor=self.ajeno, identificador=self.payload()["identificador"],
            pin="1", test_set_id="x",
        )
        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        mensaje = resp.data["errores"]["identificador"][0]
        self.assertIn("otro emisor", mensaje)
        # No se dice de quién es: el SoftwareID identifica a un tercero.
        self.assertNotIn(str(self.ajeno.numero_identificacion), mensaje)

    def test_un_identificador_jubilado_tambien_cuenta(self):
        """La fila existe aunque esté inactiva: no se puede duplicar."""
        SoftwareDian.objects.create(
            emisor=self.emisor, identificador=self.payload()["identificador"],
            pin="1", test_set_id="x", activo=False,
        )
        resp = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)

    def test_otro_identificador_si_se_acepta(self):
        self.client.post(URL, self.payload(), format="json")
        resp = self.client.post(
            URL, self.payload(identificador="otro-software-id"), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(SoftwareDian.objects.count(), 2)

    def test_sin_identificador_responde_400(self):
        resp = self.client.post(URL, self.payload(identificador=""), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("identificador", resp.data["errores"])

    def test_exige_test_set_id(self):
        """Sin TestSetId no hay habilitación posible: 400 antes de tocar nada.

        En el modelo el campo es opcional (un software ya en producción no tiene
        set de pruebas), así que sin exigirlo aquí la llamada respondía 201 con
        el fallo escondido en ``set_pruebas.error``.
        """
        datos = self.payload()
        datos.pop("test_set_id")
        resp = self.client.post(URL, datos, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("test_set_id", resp.data["errores"])
        # Ni se registró el software ni se llegó a llamar a la DIAN.
        self.assertFalse(SoftwareDian.objects.filter(emisor=self.emisor).exists())
        self.emitir.assert_not_called()

    def test_test_set_id_vacio_se_rechaza_igual(self):
        # allow_blank es False por defecto: "" no cuela donde no hay set.
        resp = self.client.post(URL, self.payload(test_set_id=""), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("test_set_id", resp.data["errores"])

    def test_no_jubila_el_software_anterior_si_falta_el_test_set_id(self):
        """El rechazo llega antes del update(activo=False), no después."""
        previo = SoftwareDian.objects.create(
            emisor=self.emisor, identificador="viejo", pin="1", activo=True,
            test_set_id="set-viejo",
        )
        datos = self.payload()
        datos.pop("test_set_id")
        self.client.post(URL, datos, format="json")

        previo.refresh_from_db()
        self.assertTrue(previo.activo)

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
