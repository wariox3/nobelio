"""Pruebas del manejo homogéneo de errores de la API."""
from botocore.exceptions import ClientError, EndpointConnectionError
from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound, ValidationError

from apps.nucleo.api import ErrorPasarela, ErrorSolicitud, exception_handler
from apps.utilidades import almacenamiento


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


def _client_error(codigo, http, mensaje="denegado"):
    """Fabrica el ClientError que botocore lanzaría con esa respuesta."""
    return ClientError(
        {
            "Error": {"Code": codigo, "Message": mensaje},
            "ResponseMetadata": {"HTTPStatusCode": http},
        },
        "HeadObject",
    )


class ErroresDeAlmacenamientoTests(SimpleTestCase):
    """Un fallo de Backblaze B2 sale como 502 y no como 500 con traceback."""

    def _data(self, exc):
        respuesta = exception_handler(exc, {})
        self.assertIsNotNone(respuesta)
        return respuesta

    def test_credenciales_invalidas_da_502(self):
        # El caso real: keyID inválido -> HeadObject 403 al comprobar si el
        # archivo ya existe (file_overwrite=False).
        r = self._data(_client_error("InvalidAccessKeyId", 403))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], almacenamiento.MENSAJE_ALMACENAMIENTO)
        self.assertEqual(r.data["errores"], {})

    def test_403_sin_codigo_reconocible_tambien_da_502(self):
        # B2 devuelve Code="403" a secas en algunas operaciones (HeadObject).
        r = self._data(_client_error("403", 403, "Forbidden"))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], almacenamiento.MENSAJE_ALMACENAMIENTO)

    def test_bucket_inexistente(self):
        r = self._data(_client_error("NoSuchBucket", 404))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], almacenamiento.MENSAJE_ALMACENAMIENTO)

    def test_error_de_conexion(self):
        r = self._data(EndpointConnectionError(endpoint_url="https://s3.x.com"))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], almacenamiento.MENSAJE_ALMACENAMIENTO)

    def test_mensaje_no_filtra_el_detalle_de_botocore(self):
        # El keyID viaja en el mensaje de botocore; la respuesta no lo repite.
        exc = _client_error("InvalidAccessKeyId", 403, "The key '0051abc' is not valid")
        r = self._data(exc)
        self.assertEqual(r.data["detail"], almacenamiento.MENSAJE_ALMACENAMIENTO)
        self.assertNotIn("0051abc", r.data["detail"])

    def test_otras_excepciones_no_se_tocan(self):
        # Una excepción ajena a botocore sigue su curso: el handler devuelve
        # None y Django la trata como 500 (o la maneja DRF si es suya).
        self.assertIsNone(exception_handler(RuntimeError("boom"), {}))
        r = self._data(NotFound())
        self.assertEqual(r.status_code, 404)
