"""Pruebas de `consulta-dian` e `importar-dian`, las acciones de la resolución."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.dian import soap
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.emisores.models import Emisor, Resolucion, SoftwareDian
from apps.nucleo.models import Ambiente

CONSULTAR = "apps.emisores.views.resolucion.dian.consultar_rangos_numeracion"


def _crear_emisor(cat, nit="901192048", **extra):
    emisor = Emisor.objects.create(
        usuario=cat["usuario"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3", **extra,
    )
    crear_certificado(emisor)
    return emisor


def _respuesta(*rangos):
    return soap.RespuestaRangos(codigo="100", descripcion="OK", rangos=list(rangos))


def _rango(prefijo="FE", numero="18764000001"):
    return soap.RangoNumeracion(
        prefijo=prefijo, numero_resolucion=numero,
        fecha_resolucion="2026-01-10", rango_desde=1, rango_hasta=5000,
        vigente_desde="2026-01-10", vigente_hasta="2028-01-10T00:00:00",
        clave_tecnica="clave-secreta",
    )


class ResolucionDianTests(APITestCase):
    url_consulta = "/api/emisores/resolucion/consulta-dian/"
    url_importar = "/api/emisores/resolucion/importar-dian/"

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(
            self.cat,
            ambiente_facturacion=Ambiente.PRODUCCION,
            ambiente_documento_equivalente=Ambiente.PRUEBAS,
        )
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.usuario)
        TipoFactura.objects.get_or_create(
            codigo="01", defaults={"id": 1, "nombre": "Factura electrónica de Venta"},
        )
        TipoFactura.objects.get_or_create(
            codigo="20", defaults={"id": 20, "nombre": "Documento equivalente P.O.S."},
        )
        self.facturacion = self._software(SoftwareDian.Tipo.FACTURACION, "sw-fe")
        self.pos = self._software(SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE, "sw-pos")

    def _software(self, tipo, identificador, emisor=None):
        return SoftwareDian.objects.create(
            emisor=emisor or self.emisor, tipo=tipo,
            identificador=identificador, pin="12345",
        )

    # --- consulta-dian ------------------------------------------------------

    def test_consulta_con_el_software_y_el_ambiente_indicados(self):
        with patch(CONSULTAR, return_value=_respuesta(_rango())) as consultar:
            resp = self.client.get(self.url_consulta, {"software": self.pos.pk})

        self.assertEqual(resp.status_code, 200, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.pos, ambiente=Ambiente.PRUEBAS,
        )
        rango = resp.data["rangos"][0]
        self.assertEqual(rango["prefijo"], "FE")
        self.assertTrue(rango["tiene_clave_tecnica"])
        self.assertNotIn("clave_tecnica", rango)
        self.assertFalse(Resolucion.objects.exists())

    def test_consulta_sin_software_es_400(self):
        resp = self.client.get(self.url_consulta)

        self.assertEqual(resp.status_code, 400, resp.data)

    def test_consulta_con_software_inexistente_es_404(self):
        resp = self.client.get(self.url_consulta, {"software": 999999})

        self.assertEqual(resp.status_code, 404, resp.data)

    def test_consulta_con_software_fuera_del_alcance_es_403(self):
        ajeno = self._software(
            SoftwareDian.Tipo.FACTURACION, "sw-ajeno",
            emisor=_crear_emisor(self.cat, nit="900123456"),
        )
        with patch(CONSULTAR) as consultar:
            resp = self.client.get(self.url_consulta, {"software": ajeno.pk})

        self.assertEqual(resp.status_code, 403, resp.data)
        consultar.assert_not_called()

    def test_el_software_de_nomina_no_tiene_rangos(self):
        nomina = self._software(SoftwareDian.Tipo.NOMINA, "sw-nomina")
        with patch(CONSULTAR) as consultar:
            resp = self.client.get(self.url_consulta, {"software": nomina.pk})

        self.assertEqual(resp.status_code, 400, resp.data)
        consultar.assert_not_called()

    # --- importar-dian ------------------------------------------------------

    def test_importa_con_facturacion_como_tipo_01(self):
        with patch(CONSULTAR, return_value=_respuesta(_rango())) as consultar:
            resp = self.client.post(
                self.url_importar, {"software": self.facturacion.pk}, format="json",
            )

        self.assertEqual(resp.status_code, 201, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRODUCCION,
        )
        resolucion = Resolucion.objects.get()
        self.assertEqual(resolucion.emisor, self.emisor)
        self.assertEqual(resolucion.tipo_factura.codigo, "01")
        self.assertEqual(resolucion.clave_tecnica, "clave-secreta")
        self.assertEqual(resolucion.consecutivo_actual, 1)
        self.assertNotIn("clave_tecnica", resp.data[0])

    def test_importa_con_documento_equivalente_como_tipo_20(self):
        with patch(CONSULTAR, return_value=_respuesta(_rango(prefijo="POS"))):
            resp = self.client.post(
                self.url_importar, {"software": self.pos.pk}, format="json",
            )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Resolucion.objects.get().tipo_factura.codigo, "20")

    def test_no_reimporta_las_que_ya_existen(self):
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            self.client.post(
                self.url_importar, {"software": self.facturacion.pk}, format="json",
            )
        with patch(CONSULTAR, return_value=_respuesta(_rango(), _rango("FE2"))):
            resp = self.client.post(
                self.url_importar, {"software": self.facturacion.pk}, format="json",
            )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual([r["prefijo"] for r in resp.data], ["FE2"])
        self.assertEqual(Resolucion.objects.count(), 2)

    def test_importar_sin_rangos_devuelve_el_mensaje_de_la_dian(self):
        respuesta = soap.RespuestaRangos(
            codigo="301", descripcion="No registra prefijos asociados", rangos=[],
        )
        with patch(CONSULTAR, return_value=respuesta):
            resp = self.client.post(
                self.url_importar, {"software": self.pos.pk}, format="json",
            )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("No registra prefijos", resp.data["detail"])

    def test_importar_sin_software_es_400(self):
        resp = self.client.post(self.url_importar, {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
