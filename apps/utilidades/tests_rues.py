"""Pruebas del cliente RUES (servicio transversal)."""
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from apps.utilidades import rues


def _respuesta_fake(payload, status_code=200):
    """Crea un mock con la interfaz mínima de requests.Response que usamos."""
    class _Resp:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    return _Resp()


REGISTRO_BANCOLOMBIA = {
    "tipo_documento": "NIT",
    "nit": "890903938",
    "id_rm": "210008396404",
    "dv": "8",
    "razon_social": "BANCOLOMBIA S.A.",
    "nom_camara": "MEDELLIN PARA ANTIOQUIA",
    "organizacion_juridica": "SOCIEDAD ANONIMA",
    "estado_matricula": "ACTIVA",
    "categoria": "SOCIEDAD ó PERSONA JURIDICA PRINCIPAL ó ESAL",
}


class ConsultarNitTests(SimpleTestCase):
    def test_nit_existente_devuelve_empresa(self):
        payload = {"registros": [REGISTRO_BANCOLOMBIA]}
        with patch.object(rues.requests, "post", return_value=_respuesta_fake(payload)):
            empresa = rues.consultar_nit("890903938")
        self.assertIsNotNone(empresa)
        self.assertEqual(empresa.razon_social, "BANCOLOMBIA S.A.")
        self.assertEqual(empresa.digito_verificacion, "8")
        self.assertTrue(empresa.activa)

    def test_busqueda_difusa_sin_coincidencia_exacta_devuelve_none(self):
        # El RUES puede devolver registros cuyo NIT NO es el consultado.
        payload = {"registros": [{"nit": "999999999", "razon_social": "OTRA EMPRESA"}]}
        with patch.object(rues.requests, "post", return_value=_respuesta_fake(payload)):
            empresa = rues.consultar_nit("890903938")
        self.assertIsNone(empresa)

    def test_sin_registros_devuelve_none(self):
        with patch.object(rues.requests, "post", return_value=_respuesta_fake({"registros": []})):
            self.assertIsNone(rues.consultar_nit("000000000"))

    def test_prefiere_el_registro_principal(self):
        sucursal = {**REGISTRO_BANCOLOMBIA, "razon_social": "BANCOLOMBIA SUCURSAL",
                    "categoria": "SUCURSAL"}
        payload = {"registros": [sucursal, REGISTRO_BANCOLOMBIA]}
        with patch.object(rues.requests, "post", return_value=_respuesta_fake(payload)):
            empresa = rues.consultar_nit("890903938")
        self.assertEqual(empresa.razon_social, "BANCOLOMBIA S.A.")

    def test_servicio_caido_lanza_no_disponible(self):
        with patch.object(rues.requests, "post", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(rues.RuesNoDisponible):
                rues.consultar_nit("890903938")

    def test_nit_vacio_devuelve_none_sin_llamar(self):
        with patch.object(rues.requests, "post") as mock_post:
            self.assertIsNone(rues.consultar_nit("  "))
        mock_post.assert_not_called()


DETALLE_BANCOLOMBIA = {
    "numero_identificacion": "890903938",
    "dv": "8",
    "razon_social": "BANCOLOMBIA S.A.",
    "estado": "ACTIVA",
    "organizacion_juridica": "SOCIEDAD ANONIMA",
    "camara": "MEDELLIN PARA ANTIOQUIA",
    "email_com": "contacto@bancolombia.com.co",
    "email_fiscal": "",
    "dir_comercial": "CR 48 26 85",
    "dir_fiscal": "",
    "tel_com_1": "6045109000",
    "tel_fiscal_1": "",
    "cod_ciiu_act_econ_pri": "6412",
    "desc_ciiu_act_econ_pri": "Bancos comerciales",
}


class ConsultarDetalleTests(SimpleTestCase):
    def test_detalle_combina_busqueda_y_detalle(self):
        busqueda = _respuesta_fake({"registros": [REGISTRO_BANCOLOMBIA]})
        detalle = _respuesta_fake({"registros": DETALLE_BANCOLOMBIA})
        with patch.object(rues.requests, "post", return_value=busqueda), \
                patch.object(rues.requests, "get", return_value=detalle):
            d = rues.consultar_detalle("890903938")
        self.assertIsNotNone(d)
        self.assertEqual(d.correo, "contacto@bancolombia.com.co")
        self.assertEqual(d.direccion, "CR 48 26 85")
        self.assertEqual(d.telefono, "6045109000")
        self.assertEqual(d.actividad_ciiu, "6412")
        self.assertTrue(d.activa)

    def test_correo_usa_fiscal_si_comercial_vacio(self):
        registro = {**DETALLE_BANCOLOMBIA, "email_com": "", "email_fiscal": "fiscal@x.co"}
        with patch.object(rues.requests, "post",
                          return_value=_respuesta_fake({"registros": [REGISTRO_BANCOLOMBIA]})), \
                patch.object(rues.requests, "get",
                             return_value=_respuesta_fake({"registros": registro})):
            d = rues.consultar_detalle("890903938")
        self.assertEqual(d.correo, "fiscal@x.co")

    def test_detalle_nit_inexistente_no_llama_al_detalle(self):
        with patch.object(rues.requests, "post",
                          return_value=_respuesta_fake({"registros": []})), \
                patch.object(rues.requests, "get") as mock_get:
            self.assertIsNone(rues.consultar_detalle("000000000"))
        mock_get.assert_not_called()
