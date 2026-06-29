"""Pruebas del manejo homogéneo de errores de la API."""
from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound, ValidationError

from apps.nucleo.api import ErrorPasarela, ErrorSolicitud, exception_handler


class ExceptionHandlerTests(SimpleTestCase):
    """Todas las respuestas de error siguen {"detail": ..., "errores": {...}}."""

    def _data(self, exc):
        respuesta = exception_handler(exc, {})
        self.assertIsNotNone(respuesta)
        self.assertEqual(set(respuesta.data.keys()), {"detail", "errores"})
        return respuesta

    def test_validation_error_por_campo(self):
        r = self._data(ValidationError({"clave": ["Este campo es obligatorio."]}))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "La solicitud no es válida.")
        self.assertEqual(r.data["errores"], {"clave": ["Este campo es obligatorio."]})

    def test_validation_error_lista(self):
        # ValidationError("texto") produce una lista; el mensaje va a detail.
        r = self._data(ValidationError("Algo salió mal."))
        self.assertEqual(r.data["detail"], "Algo salió mal.")
        self.assertEqual(r.data["errores"], {})

    def test_validation_error_non_field(self):
        r = self._data(ValidationError({"non_field_errors": ["Algo salió mal."]}))
        self.assertEqual(r.data["detail"], "Algo salió mal.")
        self.assertEqual(r.data["errores"], {"non_field_errors": ["Algo salió mal."]})

    def test_error_solicitud_mensaje_en_detail(self):
        r = self._data(ErrorSolicitud("El documento no está firmado."))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "El documento no está firmado.")
        self.assertEqual(r.data["errores"], {})

    def test_error_pasarela_502(self):
        r = self._data(ErrorPasarela("La DIAN no responde."))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], "La DIAN no responde.")
        self.assertEqual(r.data["errores"], {})

    def test_not_found(self):
        r = self._data(NotFound())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data["errores"], {})

    def test_excepcion_no_manejada_devuelve_none(self):
        # Las no-API (500) las maneja Django, no este handler.
        self.assertIsNone(exception_handler(ValueError("boom"), {}))
