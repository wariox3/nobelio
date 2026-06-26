"""Pruebas de la validación de NIT contra el RUES al crear/editar un emisor."""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.exceptions import APIException

from apps.emisores.serializers.emisor import EmisorSerializer
from apps.servicios.rues import EmpresaRues, RuesNoDisponible

_RUTA = "apps.emisores.serializers.emisor.consultar_nit"

EMPRESA = EmpresaRues(
    nit="900123456", digito_verificacion="7", razon_social="EMPRESA DEMO SAS",
    estado_matricula="ACTIVA", organizacion_juridica="SOCIEDAD ANONIMA",
    camara_comercio="MEDELLIN", categoria="PRINCIPAL", tipo_documento="NIT",
)


class ValidacionNitEmisorTests(TestCase):
    def test_nit_inexistente_invalida_el_campo(self):
        with patch(_RUTA, return_value=None):
            s = EmisorSerializer(data={"numero_identificacion": "000000000"})
            self.assertFalse(s.is_valid())
        self.assertIn("numero_identificacion", s.errors)

    def test_nit_existente_no_marca_error_en_el_campo(self):
        with patch(_RUTA, return_value=EMPRESA):
            s = EmisorSerializer(data={"numero_identificacion": "900123456"})
            s.is_valid()
        # Faltarán otros campos requeridos, pero el NIT no debe dar error.
        self.assertNotIn("numero_identificacion", s.errors)

    def test_rues_no_disponible_propaga_503(self):
        with patch(_RUTA, side_effect=RuesNoDisponible("caído")):
            s = EmisorSerializer(data={"numero_identificacion": "900123456"})
            with self.assertRaises(APIException) as ctx:
                s.is_valid()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_en_edicion_sin_cambiar_nit_no_consulta_rues(self):
        emisor = type("E", (), {"numero_identificacion": "900123456"})()
        with patch(_RUTA) as mock_consultar:
            s = EmisorSerializer(instance=emisor, data={"numero_identificacion": "900123456"})
            s.is_valid()
        mock_consultar.assert_not_called()
