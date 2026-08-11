"""El mismo NIT dado de alta en varias cuentas (una por integración).

Dos escenarios reales:

- **Canales distintos**: el cliente factura por una integración y liquida
  nómina por otra, con datos de emisor propios en cada una (correo, software,
  certificado).
- **Migración de proveedor**: hoy factura con ERP 1 y mañana con ERP 2; durante
  el traslado convive en las dos cuentas y el histórico de la primera queda
  intacto.

Lo único que no puede duplicarse es una resolución de numeración **activa**: la
DIAN autoriza un solo rango por prefijo, así que dos filas numerando a la vez
producirían consecutivos repetidos (y quemados).
"""
from datetime import date
from unittest import mock

from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.cuentas.models import Cuenta
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor, ResolucionFacturacion
from apps.seguridad.models import Usuario

NIT = "901192048"
URL_RESOLUCIONES = "/api/emisores/resolucion/"
URL_EMISORES = "/api/emisores/emisor/"


class MismoNitEnVariasCuentasTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.tipo_factura = TipoFactura.objects.create(codigo="01", nombre="Factura")

        self.erp1 = Cuenta.objects.create(nombre="RedDoc ERP")
        self.erp2 = Cuenta.objects.create(nombre="ERP XYZ")
        self.emisor1 = self.crear_emisor(self.erp1, correo="facturacion@empresa.co")
        self.emisor2 = self.crear_emisor(self.erp2, correo="nomina@empresa.co")

        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

    def crear_emisor(self, cuenta, correo, nit=NIT):
        c = self.cat
        return Emisor.objects.create(
            cuenta=cuenta,
            razon_social="Semantica Digital S.A.S",
            correo=correo,
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def payload_resolucion(self, emisor, prefijo="SETP", numero="18760000001"):
        return {
            "emisor": emisor.id,
            "tipo_factura": self.tipo_factura.id,
            "numero_resolucion": numero,
            "fecha_resolucion": "2024-01-01",
            "prefijo": prefijo,
            "rango_desde": 1,
            "rango_hasta": 5000,
            "vigente_desde": "2024-01-01",
            "vigente_hasta": "2030-01-01",
        }

    # --- El NIT convive en varias cuentas ---------------------------------

    def test_el_mismo_nit_existe_en_dos_cuentas_con_datos_propios(self):
        self.assertNotEqual(self.emisor1.id, self.emisor2.id)
        self.assertEqual(self.emisor1.numero_identificacion, self.emisor2.numero_identificacion)
        # Cada fila lleva sus propios datos: es el sentido de separarlas.
        self.assertNotEqual(self.emisor1.correo, self.emisor2.correo)

    def test_el_mismo_nit_no_se_repite_dentro_de_una_cuenta(self):
        with self.assertRaises(IntegrityError):
            self.crear_emisor(self.erp1, correo="otro@empresa.co")

    def test_el_alta_duplicada_dice_cual_es_el_emisor_que_estorba(self):
        """El 400 tiene que servir para actuar, no solo para saber que falló."""
        payload = {
            "cuenta": self.erp1.id,
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": self.cat["nit"].id,
            "numero_identificacion": NIT,
            "tipo_organizacion": self.cat["juridica"].id,
            "pais": self.cat["colombia"].id,
            "departamento": self.cat["antioquia"].id,
            "municipio": self.cat["medellin"].id,
            "direccion": "Calle 1 # 2-3",
        }
        with mock.patch(
            "apps.emisores.serializers.emisor.consultar_nit", return_value={"nit": NIT}
        ):
            resp = self.client.post(URL_EMISORES, payload, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # Cuelga del campo que hay que corregir, no de non_field_errors.
        self.assertIn("numero_identificacion", resp.data["errores"])
        mensaje = resp.data["errores"]["numero_identificacion"][0]
        self.assertNotIn("conjunto único", mensaje)
        self.assertIn(self.erp1.nombre, mensaje)
        self.assertIn(self.emisor1.razon_social, mensaje)
        self.assertIn(str(self.emisor1.pk), mensaje)

    def test_editar_un_emisor_sin_cambiar_su_nit_no_choca_consigo_mismo(self):
        with mock.patch(
            "apps.emisores.serializers.emisor.consultar_nit", return_value={"nit": NIT}
        ):
            resp = self.client.patch(
                f"{URL_EMISORES}{self.emisor1.id}/",
                {"nombre_comercial": "Semántica"},
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    # --- La numeración sigue siendo una sola ------------------------------

    def test_no_se_puede_activar_la_misma_resolucion_en_dos_cuentas(self):
        primera = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        self.assertEqual(primera.status_code, status.HTTP_201_CREATED, primera.data)

        segunda = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor2), format="json"
        )
        self.assertEqual(segunda.status_code, status.HTTP_400_BAD_REQUEST)
        # El mensaje dice dónde está ocupada, para que se sepa dónde desactivarla.
        self.assertIn("RedDoc ERP", str(segunda.data["errores"]["numero_resolucion"]))

    def test_un_prefijo_distinto_si_puede_convivir(self):
        # Facturación por un canal y nómina por otro: rangos DIAN distintos.
        self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        otra = self.client.post(
            URL_RESOLUCIONES,
            self.payload_resolucion(self.emisor2, prefijo="NOMI", numero="18760000002"),
            format="json",
        )
        self.assertEqual(otra.status_code, status.HTTP_201_CREATED, otra.data)

    def test_migrar_de_erp_desactivando_la_resolucion_anterior(self):
        vieja = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        self.assertEqual(vieja.status_code, status.HTTP_201_CREATED)

        # El cliente deja ERP 1: se desactiva allí y el rango queda libre.
        cierre = self.client.patch(
            f"{URL_RESOLUCIONES}{vieja.data['id']}/", {"activa": False}, format="json"
        )
        self.assertEqual(cierre.status_code, status.HTTP_200_OK, cierre.data)

        nueva = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor2), format="json"
        )
        self.assertEqual(nueva.status_code, status.HTTP_201_CREATED, nueva.data)

        # El histórico de la cuenta anterior sigue ahí, solo que inactivo.
        self.assertEqual(
            ResolucionFacturacion.objects.filter(emisor=self.emisor1).count(), 1
        )

    def test_reactivar_la_vieja_tras_migrar_tampoco_se_permite(self):
        vieja = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        self.client.patch(
            f"{URL_RESOLUCIONES}{vieja.data['id']}/", {"activa": False}, format="json"
        )
        self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor2), format="json"
        )

        resp = self.client.patch(
            f"{URL_RESOLUCIONES}{vieja.data['id']}/", {"activa": True}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_editar_la_propia_resolucion_no_choca_consigo_misma(self):
        creada = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        resp = self.client.patch(
            f"{URL_RESOLUCIONES}{creada.data['id']}/",
            {"rango_hasta": 9000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_fecha_resolucion_se_guarda(self):
        # Guarda de cordura del payload usado por el resto de pruebas.
        creada = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor1), format="json"
        )
        resolucion = ResolucionFacturacion.objects.get(pk=creada.data["id"])
        self.assertEqual(resolucion.fecha_resolucion, date(2024, 1, 1))
