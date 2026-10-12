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
        self.assertEqual(resp.data["detail"], "No existe un software con id 999999.")

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

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data, {"creadas": 2})
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

        self.assertEqual(resp.status_code, 200, resp.data)
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

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data, {"creadas": 1})
        self.assertEqual(Resolucion.objects.get().prefijo, "FE")

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

        self.assertEqual(resp.status_code, 200, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRUEBAS,
        )
        resolucion = Resolucion.objects.get()
        self.assertEqual(resolucion.emisor, self.emisor)
        self.assertEqual(resolucion.tipo_factura.codigo, "01")
        self.assertEqual(resolucion.clave_tecnica, "clave-secreta")
        self.assertEqual(resolucion.consecutivo_actual, 1)

    def test_no_reimporta_las_que_ya_existen(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            self._importar_emisor()
            repetida = self._importar_emisor()
        with patch(CONSULTAR, return_value=_respuesta(_rango(), _rango("FE2"))):
            resp = self._importar_emisor()

        self.assertEqual(repetida.status_code, 200, repetida.data)
        self.assertEqual(repetida.data, {"creadas": 0})
        self.assertEqual(resp.data, {"creadas": 1})
        self.assertEqual(Resolucion.objects.count(), 2)

    def test_importar_con_emisor_inexistente_es_404_con_su_id(self):
        resp = self.client.post(self.url_importar, {"emisor": 999999}, format="json")

        self.assertEqual(resp.status_code, 404, resp.data)
        self.assertEqual(resp.data["detail"], "No existe un emisor con id 999999.")

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

    def _validar(self, *resoluciones, emisor=None):
        return self.client.post(
            self.url_validar,
            {
                "emisor": (emisor or self.emisor).pk,
                "resoluciones": list(resoluciones),
            },
            format="json",
        )

    def _pedida(self, **cambios):
        """La resolución de `_rango()` tal como la mandaría el ERP."""
        return {
            "numero_resolucion": "18764000001", "prefijo": "FE",
            "rango_desde": 1, "rango_hasta": 5000,
            "vigente_desde": "2026-01-10", "vigente_hasta": "2028-01-10",
            **cambios,
        }

    def test_valida_importando_antes_y_devuelve_lo_registrado(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())) as consultar:
            resp = self._validar(self._pedida())

        self.assertEqual(resp.status_code, 200, resp.data)
        consultar.assert_called_once_with(
            self.emisor, software=self.facturacion, ambiente=Ambiente.PRUEBAS,
        )
        self.assertEqual(Resolucion.objects.count(), 1)
        self.assertEqual(resp.json(), [{
            "numero_resolucion": "18764000001",
            "validado": True,
            "inconsistencia": False,
            "prefijo": "FE",
            "rango_desde": 1, "rango_hasta": 5000,
            "vigente_desde": "2026-01-10", "vigente_hasta": "2028-01-10",
        }])

    def test_cada_dato_que_no_coincide_marca_inconsistencia(self):
        self._aceptar(self.facturacion)
        distintos = {
            "rango_desde": 2, "rango_hasta": 4000,
            "vigente_desde": "2026-01-11", "vigente_hasta": "2029-01-10",
        }
        for campo, valor in distintos.items():
            with self.subTest(campo=campo):
                with patch(CONSULTAR, return_value=_respuesta(_rango())):
                    resp = self._validar(self._pedida(**{campo: valor}))

                self.assertEqual(resp.status_code, 200, resp.data)
                entrada = resp.json()[0]
                self.assertTrue(entrada["validado"])
                self.assertTrue(entrada["inconsistencia"])
                # Se devuelve lo registrado, no lo que se envió.
                self.assertEqual(entrada[campo], self._pedida()[campo])

    def test_solo_se_compara_lo_que_se_envia(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            resp = self._validar({"numero_resolucion": "18764000001"})

        entrada = resp.json()[0]
        self.assertTrue(entrada["validado"])
        self.assertFalse(entrada["inconsistencia"])
        self.assertEqual(entrada["prefijo"], "FE")
        self.assertEqual(entrada["rango_hasta"], 5000)

    def test_la_que_el_emisor_no_tiene_no_valida(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            resp = self._validar(
                self._pedida(), self._pedida(numero_resolucion="99999"),
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        valida, ausente = resp.json()
        self.assertTrue(valida["validado"])
        self.assertEqual(ausente, {
            "numero_resolucion": "99999",
            "validado": False,
            "inconsistencia": False,
            "prefijo": "FE",
            "rango_desde": None, "rango_hasta": None,
            "vigente_desde": None, "vigente_hasta": None,
        })

    def test_el_prefijo_enviado_tiene_que_coincidir(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            resp = self._validar(self._pedida(prefijo="OTRO"))

        self.assertFalse(resp.json()[0]["validado"])

    def test_sin_prefijo_prefiere_el_registro_que_coincide(self):
        self._aceptar(self.facturacion)
        otro = _rango("AA")
        otro.rango_hasta = 9000
        with patch(CONSULTAR, return_value=_respuesta(otro, _rango())):
            pedida = self._pedida()
            del pedida["prefijo"]
            resp = self._validar(pedida)

        entrada = resp.json()[0]
        self.assertEqual(entrada["prefijo"], "FE")
        self.assertFalse(entrada["inconsistencia"])

    def test_valida_la_registrada_que_la_dian_no_devuelve(self):
        """Se contrasta con la base: una cargada a mano también cuenta."""
        self._aceptar(self.facturacion)
        Resolucion.objects.create(
            emisor=self.emisor, tipo_factura=TipoFactura.objects.get(codigo="01"),
            prefijo="MAN", numero_resolucion="555", fecha_resolucion="2026-01-01",
            rango_desde=1, rango_hasta=100,
            vigente_desde="2026-01-01", vigente_hasta="2027-01-01",
        )
        with patch(CONSULTAR, return_value=_respuesta(_rango())):
            resp = self._validar({"numero_resolucion": "555"})

        self.assertTrue(resp.json()[0]["validado"])

    def test_sin_resoluciones_no_hace_nada(self):
        self._aceptar(self.facturacion)
        with patch(CONSULTAR) as consultar:
            vacia = self._validar()
            ausente = self.client.post(
                self.url_validar, {"emisor": self.emisor.pk}, format="json",
            )

        for resp in (vacia, ausente):
            self.assertEqual(resp.status_code, 200, resp.data)
            self.assertEqual(resp.json(), [])
        consultar.assert_not_called()
        self.assertFalse(Resolucion.objects.exists())

    def test_validar_sin_software_aceptado_es_400(self):
        with patch(CONSULTAR) as consultar:
            resp = self._validar(self._pedida())

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["errores"][0]["codigo"], "software_no_aceptado")
        consultar.assert_not_called()

    def test_validar_sin_rangos_en_la_dian_devuelve_su_mensaje(self):
        self._aceptar(self.facturacion)
        sin_rangos = soap.RespuestaRangos(
            codigo="301", descripcion="No registra prefijos asociados", rangos=[],
        )
        with patch(CONSULTAR, return_value=sin_rangos):
            resp = self._validar(self._pedida())

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["detail"], "No registra prefijos asociados")

    def test_validar_con_emisor_fuera_del_alcance_es_403(self):
        ajeno = _crear_emisor(self.cat, nit="900123456")
        with patch(CONSULTAR) as consultar:
            con_lista = self._validar(self._pedida(), emisor=ajeno)
            sin_lista = self._validar(emisor=ajeno)

        self.assertEqual(con_lista.status_code, 403, con_lista.data)
        self.assertEqual(sin_lista.status_code, 403, sin_lista.data)
        consultar.assert_not_called()

    def test_validar_con_emisor_inexistente_es_404(self):
        resp = self.client.post(self.url_validar, {"emisor": 999999}, format="json")

        self.assertEqual(resp.status_code, 404, resp.data)

    def test_validar_exige_emisor_y_numero_de_resolucion(self):
        sin_emisor = self.client.post(self.url_validar, {}, format="json")
        sin_numero = self._validar({"prefijo": "FE"})

        self.assertEqual(sin_emisor.status_code, 400, sin_emisor.data)
        self.assertEqual(sin_numero.status_code, 400, sin_numero.data)
        self.assertIn("numero_resolucion", sin_numero.data["errores"][0]["mensaje"])
