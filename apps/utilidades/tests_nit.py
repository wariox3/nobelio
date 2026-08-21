"""Pruebas del dígito de verificación del NIT."""
from django.test import SimpleTestCase

from apps.utilidades.nit import digito_verificacion


class DigitoVerificacionTests(SimpleTestCase):
    def test_nit_publico_de_control(self):
        """El NIT de la propia DIAN, 800197268-4, ancla el algoritmo.

        Es un valor comprobable fuera del código: si el módulo 11 se
        implementara mal, este caso lo delata.
        """
        self.assertEqual(digito_verificacion("800197268"), "4")

    def test_valores_de_regresion(self):
        # Calculados con el algoritmo ya anclado por el caso anterior; fijan el
        # resultado para que un cambio en los pesos no pase inadvertido.
        for nit, esperado in {"901192048": "4", "899999061": "9"}.items():
            with self.subTest(nit=nit):
                self.assertEqual(digito_verificacion(nit), esperado)

    def test_ignora_puntos_y_guiones(self):
        self.assertEqual(digito_verificacion("800.197.268"), "4")

    def test_numero_vacio_o_no_numerico(self):
        self.assertEqual(digito_verificacion(""), "")
        self.assertEqual(digito_verificacion(None), "")
        self.assertEqual(digito_verificacion("abc"), "")

    def test_numero_demasiado_largo(self):
        self.assertEqual(digito_verificacion("1" * 16), "")
