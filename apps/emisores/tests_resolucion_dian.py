"""Pruebas de `consulta-dian`, `importar-dian` y `validar`, las acciones de la resolución."""
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import override_settings
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
    url_validar = "/api/emisores/resolucion/validar/"

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat, habilitado_facturacion=True)
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

    def _software(self, tipo, identificador, emisor=None, **extra):
        return SoftwareDian.objects.create(
            emisor=emisor or self.emisor, tipo=tipo,
            identificador=identificador, pin="12345", **extra,
        )

    def _aceptar(self, software):
        software.set_pruebas_aceptado = True
        software.save(update_fields=["set_pruebas_aceptado"])

    # --- consulta-dian ------------------------------------------------------

    def test_consulta_con_el_software_indicado(self):
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

    @override_settings(ENV="prod")
    def test_en_prod_la_operacion_habilitada_consulta_en_produccion(self):
        with patch(CONSULTAR, return_value=_respuesta()) as consultar:
            self.client.get(self.url_consulta, {"software": self.facturacion.pk})

        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRODUCCION,
        )

    @override_settings(ENV="prod")
    def test_en_prod_la_operacion_sin_habilitar_consulta_en_pruebas(self):
        with patch(CONSULTAR, return_value=_respuesta()) as consultar:
            self.client.get(self.url_consulta, {"software": self.pos.pk})

        consultar.assert_called_once_with(
            self.emisor, software=self.pos, ambiente=Ambiente.PRUEBAS,
        )

    def test_fuera_de_prod_consulta_siempre_en_pruebas(self):
        for entorno in ("dev", "test"):
            with self.subTest(entorno=entorno), override_settings(ENV=entorno):
                with patch(CONSULTAR, return_value=_respuesta()) as consultar:
                    self.client.get(
                        self.url_consulta, {"software": self.facturacion.pk}
                    )
                consultar.assert_called_once_with(
                    self.emisor, software=self.facturacion,
                    ambiente=Ambiente.PRUEBAS,
                )

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

    def _importar_emisor(self, emisor=None):
        return self.client.post(
            self.url_importar, {"emisor": (emisor or self.emisor).pk},
            format="json",
        )

    def _por_software(self, respuestas):
        """``consultar_rangos_numeracion`` que responde según el software."""
        return lambda emisor, software, ambiente: respuestas[software.pk]

    def test_importa_las_de_todos_los_softwares_aceptados(self):
        self._aceptar(self.facturacion)
        self._aceptar(self.pos)
        respuestas = {
            self.facturacion.pk: _respuesta(_rango()),
            self.pos.pk: _respuesta(_rango("POS", "18764000009")),
        }
        with patch(CONSULTAR, side_effect=self._por_software(respuestas)) as consultar:
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(consultar.call_count, 2)
        self.assertEqual(
            {r.prefijo: r.tipo_factura.codigo for r in Resolucion.objects.all()},
            {"FE": "01", "POS": "20"},
        )

    def test_por_emisor_solo_consulta_los_aceptados(self):
        self._aceptar(self.facturacion)
        self._aceptar(self._software(SoftwareDian.Tipo.NOMINA, "sw-nomina"))
        with patch(CONSULTAR, return_value=_respuesta(_rango())) as consultar:
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 201, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRUEBAS,
        )

    def test_por_emisor_sin_software_aceptado_es_400(self):
        with patch(CONSULTAR) as consultar:
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["errores"][0]["codigo"], "software_no_aceptado")
        consultar.assert_not_called()

    def test_por_emisor_importa_lo_del_software_que_si_trae_rangos(self):
        self._aceptar(self.facturacion)
        self._aceptar(self.pos)
        respuestas = {
            self.facturacion.pk: _respuesta(_rango()),
            self.pos.pk: soap.RespuestaRangos(
                codigo="301", descripcion="No registra prefijos asociados",
                rangos=[],
            ),
        }
        with patch(CONSULTAR, side_effect=self._por_software(respuestas)):
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual([r["prefijo"] for r in resp.data], ["FE"])

    def test_por_emisor_sin_rangos_en_ninguno_devuelve_el_mensaje(self):
        self._aceptar(self.facturacion)
        self._aceptar(self.pos)
        sin_rangos = soap.RespuestaRangos(
            codigo="301", descripcion="No registra prefijos asociados", rangos=[],
        )
        with patch(CONSULTAR, return_value=sin_rangos):
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["detail"], "No registra prefijos asociados")

    def test_si_la_dian_falla_con_un_software_no_se_importa_nada(self):
        self._aceptar(self.facturacion)
        self._aceptar(self.pos)

        def consultar(emisor, software, ambiente):
            if software.pk == self.facturacion.pk:
                raise requests.ConnectionError("sin red")
            return _respuesta(_rango("POS"))

        with patch(CONSULTAR, side_effect=consultar):
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 502, resp.data)
        self.assertFalse(Resolucion.objects.exists())

    def test_por_emisor_fuera_del_alcance_es_403(self):
        ajeno = _crear_emisor(self.cat, nit="900123456")
        with patch(CONSULTAR) as consultar:
            resp = self._importar_emisor(ajeno)

        self.assertEqual(resp.status_code, 403, resp.data)
        consultar.assert_not_called()

    def test_importa_con_facturacion_como_tipo_01(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())) as consultar:
            resp = self._importar_emisor()

        self.assertEqual(resp.status_code, 201, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRUEBAS,
        )
        resolucion = Resolucion.objects.get()
        self.assertEqual(resolucion.emisor, self.emisor)
        self.assertEqual(resolucion.tipo_factura.codigo, "01")
        self.assertEqual(resolucion.clave_tecnica, "clave-secreta")
        self.assertEqual(resolucion.consecutivo_actual, 1)
        self.assertNotIn("clave_tecnica", resp.data[0])

    def test_no_reimporta_las_que_ya_existen(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            self._importar_emisor()
            repetida = self._importar_emisor()
        with patch(CONSULTAR, return_value=_respuesta(_rango(), _rango("FE2"))):
            resp = self._importar_emisor()

        self.assertEqual(repetida.status_code, 200, repetida.data)
        self.assertEqual(repetida.data, [])
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual([r["prefijo"] for r in resp.data], ["FE2"])
        self.assertEqual(Resolucion.objects.count(), 2)

    def test_importar_sin_emisor_es_400(self):
        resp = self.client.post(self.url_importar, {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)

    def test_importar_ya_no_recibe_software(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR) as consultar:
            resp = self.client.post(
                self.url_importar, {"software": self.facturacion.pk}, format="json",
            )

        self.assertEqual(resp.status_code, 400, resp.data)
        consultar.assert_not_called()

    # --- validar ------------------------------------------------------------

    def _validar(self, numero="18764000001", tipo="facturacion", emisor=None):
        return self.client.post(
            self.url_validar,
            {
                "emisor": (emisor or self.emisor).pk,
                "tipo_software": tipo,
                "numero_resolucion": numero,
            },
            format="json",
        )

    def test_valida_importa_y_devuelve_la_resolucion(self):
        self._aceptar(self.facturacion)
        rangos = _respuesta(_rango(), _rango("FE2", "18764000002"))
        with patch(CONSULTAR, return_value=rangos) as consultar:
            resp = self._validar()

        self.assertEqual(resp.status_code, 200, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRUEBAS,
        )
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["numero_resolucion"], "18764000001")
        self.assertEqual(resp.data[0]["prefijo"], "FE")
        self.assertNotIn("clave_tecnica", resp.data[0])
        # Hace el importar completo: entra también la que no se preguntó.
        self.assertEqual(Resolucion.objects.count(), 2)
        self.assertEqual(
            Resolucion.objects.get(prefijo="FE").clave_tecnica, "clave-secreta"
        )

    def test_valida_la_que_ya_estaba_importada_sin_duplicarla(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            self._validar()
            resp = self._validar()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Resolucion.objects.count(), 1)

    def test_devuelve_todos_los_prefijos_de_la_resolucion(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango(), _rango("FE2"))):
            resp = self._validar()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([r["prefijo"] for r in resp.data], ["FE", "FE2"])

    def test_sin_software_aceptado_de_ese_tipo_es_400(self):
        # El emisor tiene el software, pero sin el Set de Pruebas aceptado.
        with patch(CONSULTAR) as consultar:
            resp = self._validar()

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["errores"][0]["codigo"], "software_no_aceptado")
        self.assertIn("facturación electrónica", resp.data["detail"])
        consultar.assert_not_called()

    def test_el_software_aceptado_de_otro_tipo_no_sirve(self):
        self._aceptar(self.pos)
        with patch(CONSULTAR) as consultar:
            resp = self._validar()

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["errores"][0]["codigo"], "software_no_aceptado")
        consultar.assert_not_called()

    def test_resolucion_que_la_dian_no_devuelve_es_400(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            resp = self._validar(numero="99999")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(
            resp.data["errores"][0]["codigo"], "resolucion_no_asociada"
        )
        self.assertIn("99999", resp.data["detail"])
        self.assertFalse(Resolucion.objects.exists())

    def test_la_guardada_que_la_dian_ya_no_devuelve_no_valida(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            self._validar()
        sin_rangos = soap.RespuestaRangos(
            codigo="301", descripcion="No registra prefijos asociados", rangos=[],
        )
        with patch(CONSULTAR, return_value=sin_rangos):
            resp = self._validar()

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(
            resp.data["errores"][0]["codigo"], "resolucion_no_asociada"
        )

    def test_validar_con_nomina_es_400(self):
        self._aceptar(self._software(SoftwareDian.Tipo.NOMINA, "sw-nomina"))
        with patch(CONSULTAR) as consultar:
            resp = self._validar(tipo="nomina")

        self.assertEqual(resp.status_code, 400, resp.data)
        consultar.assert_not_called()

    def test_validar_con_emisor_fuera_del_alcance_es_400(self):
        ajeno = _crear_emisor(self.cat, nit="900123456")
        self._aceptar(self._software(
            SoftwareDian.Tipo.FACTURACION, "sw-ajeno", emisor=ajeno,
        ))
        with patch(CONSULTAR) as consultar:
            resp = self._validar(emisor=ajeno)

        self.assertEqual(resp.status_code, 400, resp.data)
        consultar.assert_not_called()

    def test_validar_exige_sus_tres_campos(self):
        resp = self.client.post(self.url_validar, {}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        mensajes = " ".join(e["mensaje"] for e in resp.data["errores"])
        for campo in ("emisor", "tipo_software", "numero_resolucion"):
            self.assertIn(campo, mensajes)
