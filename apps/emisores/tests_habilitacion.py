"""Pruebas de ``crear-habilitacion/``: registrar software y sembrar la resolución.

Repone lo que se perdió en `ff3eb34`, que borró `tests_habilitar.py` al
reescribir el endpoint. No es una restauración: aquel fichero probaba una API
que ya no existe —`ResolucionFacturacion`, `emitir_set_pruebas`— y estas prueban
la de ahora, que registra el software del emisor y le siembra la resolución del
Set de Pruebas de la DIAN.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.emisores.models import Certificado, Emisor, Resolucion, SoftwareDian
from apps.seguridad.models import Usuario

URL = "/api/emisores/emisor/crear-habilitacion/"


class HabilitacionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cat = crear_catalogos_minimos()
        cls.cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        cls.otra_cuenta = Cuenta.objects.create(nombre="ERP ajeno")
        cls.emisor = cls._crear_emisor(cls.cuenta, "901192048")
        cls.ajeno = cls._crear_emisor(cls.otra_cuenta, "900123456")

        cls.admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        # El endpoint siembra la resolución del Set de Pruebas y para eso hace
        # un `TipoFactura.objects.get(codigo="01")` directo: sin el catálogo
        # cargado no da un 400 sino un 500. En un despliegue real el catálogo
        # está; en la base de pruebas hay que ponerlo.
        TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura de Venta"}
        )

    @classmethod
    def _crear_emisor(cls, cuenta, nit):
        c = cls.cat
        emisor = Emisor.objects.create(
            cuenta=cuenta, razon_social="Semantica Digital S.A.S",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )
        # El certificado va antes que el software en el flujo: es lo que firma
        # la consulta de numeración y los documentos del Set de Pruebas.
        crear_certificado(emisor)
        return emisor

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def _payload(self, emisor=None, **extra):
        datos = {
            "emisor": (emisor or self.emisor).id,
            "tipo": SoftwareDian.Tipo.FACTURACION,
            "identificador": "56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
            "pin": "12345",
        }
        datos.update(extra)
        return datos

    # --- El camino feliz ---------------------------------------------------

    def test_registra_el_software_y_siembra_la_resolucion(self):
        resp = self.client.post(URL, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        software = SoftwareDian.objects.get(emisor=self.emisor)
        self.assertTrue(software.activo)
        self.assertEqual(software.tipo, SoftwareDian.Tipo.FACTURACION)

        # La resolución del Set de Pruebas de la DIAN, con su clave técnica.
        resolucion = Resolucion.objects.get(emisor=self.emisor, prefijo="SETP")
        self.assertEqual(resolucion.numero_resolucion, "18760000001")
        self.assertEqual(resolucion.rango_desde, 990000000)
        self.assertTrue(resolucion.activa)

    def test_repetirlo_no_duplica_nada(self):
        """El endpoint se llama varias veces mientras se prueba la habilitación."""
        self.client.post(URL, self._payload(), format="json")
        resp = self.client.post(URL, self._payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(SoftwareDian.objects.filter(emisor=self.emisor).count(), 1)
        self.assertEqual(Resolucion.objects.filter(emisor=self.emisor).count(), 1)

    def test_registrar_otro_software_jubila_el_anterior_del_mismo_tipo(self):
        self.client.post(URL, self._payload(), format="json")
        self.client.post(
            URL, self._payload(identificador="otro-software-id"), format="json"
        )

        activos = SoftwareDian.objects.filter(emisor=self.emisor, activo=True)
        self.assertEqual(activos.count(), 1)
        self.assertEqual(activos.get().identificador, "otro-software-id")

    def test_el_software_de_nomina_no_estorba_al_de_facturacion(self):
        """Cada operación se habilita por separado y convive con la otra.

        Es la razón de que solo se desactiven los del **mismo tipo**: registrar
        el de facturación no puede dejar al emisor sin poder emitir nómina.
        """
        nomina = SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.NOMINA,
            identificador="software-de-nomina", pin="999", activo=True,
        )
        self.client.post(URL, self._payload(), format="json")

        nomina.refresh_from_db()
        self.assertTrue(nomina.activo)

    # --- Lo que tiene que cortar -------------------------------------------

    def test_sin_emisor_en_el_cuerpo(self):
        payload = self._payload()
        del payload["emisor"]
        resp = self.client.post(URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_emisor_inexistente(self):
        resp = self.client.post(URL, self._payload(), format="json")  # calienta
        payload = self._payload()
        payload["emisor"] = 999999
        resp = self.client.post(URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sin_certificado_activo_no_se_habilita(self):
        Certificado.objects.filter(emisor=self.emisor).update(activo=False)
        resp = self.client.post(URL, self._payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("certificado", resp.data["detail"].lower())
        self.assertFalse(SoftwareDian.objects.filter(emisor=self.emisor).exists())

    def test_con_el_certificado_vencido_tampoco(self):
        Certificado.objects.filter(emisor=self.emisor).update(
            vigente_hasta=timezone.localdate() - timedelta(days=1)
        )
        resp = self.client.post(URL, self._payload(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SoftwareDian.objects.filter(emisor=self.emisor).exists())

    def test_el_pin_es_obligatorio(self):
        payload = self._payload()
        del payload["pin"]
        resp = self.client.post(URL, payload, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pin", resp.data["errores"])

    def test_nada_se_guarda_si_el_software_no_valida(self):
        """La resolución va dentro de la misma transacción que el software."""
        payload = self._payload()
        del payload["identificador"]
        self.client.post(URL, payload, format="json")

        self.assertFalse(Resolucion.objects.filter(emisor=self.emisor).exists())
        self.assertFalse(SoftwareDian.objects.filter(emisor=self.emisor).exists())
