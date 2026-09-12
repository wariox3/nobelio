"""Pruebas de `crear-documento-prueba`, la acción de la resolución."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.emisores.models import Emisor, Resolucion
from apps.nucleo.models import Ambiente


def _crear_emisor(cat, nit="901192048"):
    emisor = Emisor.objects.create(
        usuario=cat["usuario"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )
    crear_certificado(emisor)
    return emisor


class CrearDocumentoPruebaTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.usuario)
        self.tipo_01, _ = TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura electrónica de Venta"},
        )
        self.resolucion = self._resolucion(self.tipo_01)

    def _resolucion(self, tipo_factura, emisor=None, **extra):
        datos = dict(
            emisor=emisor or self.emisor, tipo_factura=tipo_factura,
            numero_resolucion="18760000001", fecha_resolucion="2026-06-29",
            prefijo="SETP", rango_desde=990000000, rango_hasta=990000002,
            vigente_desde="2019-01-19", vigente_hasta="2030-01-19", activa=True,
        )
        datos.update(extra)
        return Resolucion.objects.create(**datos)

    def _url(self, resolucion=None):
        pk = (resolucion or self.resolucion).pk
        return f"/api/emisores/resolucion/{pk}/crear-documento-prueba/"

    # --- Camino feliz -------------------------------------------------------

    def test_crea_una_factura_en_borrador(self):
        resp = self.client.post(self._url(), {}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["documento_tipo"], DocumentoTipo.Codigo.FACTURA_VENTA)
        self.assertEqual(resp.data["consecutivo"], 990000000)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.BORRADOR)

        documento = Documento.objects.get(pk=resp.data["id"])
        self.assertEqual(documento.resolucion_id, self.resolucion.id)
        self.assertEqual(documento.prefijo, "SETP")
        # El adquiriente es el propio emisor, igual que en el sembrado.
        self.assertEqual(
            documento.adquiriente.numero_identificacion,
            self.emisor.numero_identificacion,
        )

    def test_sin_consecutivo_toma_el_siguiente_libre(self):
        primero = self.client.post(self._url(), {}, format="json")
        segundo = self.client.post(self._url(), {}, format="json")

        self.assertEqual(primero.data["consecutivo"], 990000000)
        self.assertEqual(segundo.data["consecutivo"], 990000001)

    def test_respeta_el_consecutivo_que_se_le_pasa(self):
        resp = self.client.post(
            self._url(), {"consecutivo": 990000002}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["consecutivo"], 990000002)
        # Y el siguiente libre pasa a estar fuera del rango.
        agotado = self.client.post(self._url(), {}, format="json")
        self.assertEqual(agotado.status_code, 400)
        self.assertIn("990000003", agotado.data["detail"])

    def test_el_tipo_sale_de_la_resolucion(self):
        """Una resolución de documento soporte da un documento soporte."""
        tipo_05, _ = TipoFactura.objects.get_or_create(
            codigo="05", defaults={"nombre": "Documento soporte"},
        )
        soporte = self._resolucion(
            tipo_05, numero_resolucion="18760000002", prefijo="DS",
        )

        resp = self.client.post(self._url(soporte), {}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(
            resp.data["documento_tipo"], DocumentoTipo.Codigo.DOCUMENTO_SOPORTE
        )

    # --- Lo que tiene que cortar --------------------------------------------

    def test_rechaza_un_consecutivo_fuera_del_rango(self):
        resp = self.client.post(
            self._url(), {"consecutivo": 999999999}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("no cabe", resp.data["detail"])
        self.assertFalse(Documento.objects.exists())

    def test_rechaza_un_consecutivo_ya_usado(self):
        self.client.post(self._url(), {"consecutivo": 990000000}, format="json")

        resp = self.client.post(
            self._url(), {"consecutivo": 990000000}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(Documento.objects.count(), 1)

    def test_rechaza_un_consecutivo_que_no_es_numero(self):
        resp = self.client.post(self._url(), {"consecutivo": "abc"}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("número entero", resp.data["detail"])

    def test_rechaza_una_resolucion_inactiva(self):
        self.resolucion.activa = False
        self.resolucion.save(update_fields=["activa"])

        resp = self.client.post(self._url(), {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("inactiva", resp.data["detail"])

    def test_rechaza_si_el_emisor_ya_esta_en_produccion(self):
        """Gastaría un consecutivo de la numeración real, que no se recupera."""
        self.emisor.ambiente_facturacion = Ambiente.PRODUCCION
        self.emisor.save(update_fields=["ambiente_facturacion"])

        resp = self.client.post(self._url(), {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("producción", resp.data["detail"])
        self.assertFalse(Documento.objects.exists())

    def test_rechaza_una_numeracion_que_no_lleva_documento_propio(self):
        """Las notas heredan el número del documento que corrigen."""
        tipo_91, _ = TipoFactura.objects.get_or_create(
            codigo="91", defaults={"nombre": "Nota Crédito"},
        )
        notas = self._resolucion(
            tipo_91, numero_resolucion="18760000003", prefijo="NC",
        )

        resp = self.client.post(self._url(notas), {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("no corresponde a ningún documento", resp.data["detail"])

    def test_no_alcanza_la_resolucion_de_otro_emisor(self):
        otro = _crear_emisor(self.cat, nit="800197268")
        ajena = self._resolucion(
            self.tipo_01, emisor=otro, numero_resolucion="18760000004",
        )

        resp = self.client.post(self._url(ajena), {}, format="json")

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Documento.objects.exists())
