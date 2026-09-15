"""Pruebas del manejo homogéneo de errores de la API."""
from botocore.exceptions import ClientError, EndpointConnectionError
from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, NotFound, ValidationError

from apps.nucleo.api import (
    ErrorPasarela,
    ErrorSolicitud,
    cuerpo_de_error,
    exception_handler,
)
from apps.nucleo.serializers import (
    CODIGO_CAMPO_DESCONOCIDO,
    CODIGO_CAMPO_SOLO_LECTURA,
    CODIGO_OBLIGATORIO,
    MENSAJE_CAMPO_DESCONOCIDO,
    MENSAJE_CAMPO_SOLO_LECTURA,
    EstructuraEstricta,
)
from apps.utilidades import almacenamiento


class ExceptionHandlerTests(SimpleTestCase):
    """Todo error sale como {"detail": ..., "errores": [{"codigo", "mensaje"}]}.

    La lista nunca va vacía: hasta un 404 lleva su código, para que el cliente
    decida por `codigo` en todos los casos y no tenga que mirar el status y el
    texto según de dónde venga el fallo.
    """

    def _data(self, exc):
        respuesta = exception_handler(exc, {})
        self.assertIsNotNone(respuesta)
        self.assertEqual(set(respuesta.data.keys()), {"detail", "errores"})
        self.assertTrue(respuesta.data["errores"])
        for error in respuesta.data["errores"]:
            self.assertEqual(set(error), {"codigo", "mensaje"})
        return respuesta

    def test_validation_error_por_campo(self):
        r = self._data(ValidationError({"clave": ["Este campo es obligatorio."]}))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "La solicitud no es válida.")
        self.assertEqual(r.data["errores"], [
            {"codigo": "invalid", "mensaje": "clave: Este campo es obligatorio."},
        ])

    def test_el_campo_anidado_lleva_su_ruta_completa(self):
        """La lista es plana, pero el mensaje sigue diciendo dónde está."""
        r = self._data(ValidationError({
            "detalles": [{}, {"impuestos": [
                {"tributo": [ErrorDetail("Falta.", code="required")]},
            ]}],
            "adquiriente": {"pais": [ErrorDetail("No existe.", code="does_not_exist")]},
        }))
        self.assertEqual(r.data["errores"], [
            {"codigo": "required", "mensaje": "detalles[1].impuestos[0].tributo: Falta."},
            {"codigo": "does_not_exist", "mensaje": "adquiriente.pais: No existe."},
        ])

    def test_validation_error_lista(self):
        # ValidationError("texto") produce una lista; el mensaje va a detail.
        r = self._data(ValidationError("Algo salió mal."))
        self.assertEqual(r.data["detail"], "Algo salió mal.")
        self.assertEqual(
            r.data["errores"], [{"codigo": "invalid", "mensaje": "Algo salió mal."}]
        )

    def test_validation_error_non_field(self):
        """Sin campo, el mensaje va sin ruta."""
        r = self._data(ValidationError({"non_field_errors": ["Algo salió mal."]}))
        self.assertEqual(r.data["detail"], "Algo salió mal.")
        self.assertEqual(
            r.data["errores"], [{"codigo": "invalid", "mensaje": "Algo salió mal."}]
        )

    def test_error_solicitud_mensaje_en_detail(self):
        r = self._data(ErrorSolicitud("El documento no está firmado."))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "El documento no está firmado.")
        self.assertEqual(r.data["errores"], [
            {"codigo": "solicitud_invalida", "mensaje": "El documento no está firmado."},
        ])

    def test_error_pasarela_502(self):
        r = self._data(ErrorPasarela("La DIAN no responde."))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data["detail"], "La DIAN no responde.")
        self.assertEqual(r.data["errores"], [
            {"codigo": "error_pasarela", "mensaje": "La DIAN no responde."},
        ])

    def test_not_found(self):
        r = self._data(NotFound())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(
            r.data["errores"], [{"codigo": "not_found", "mensaje": r.data["detail"]}]
        )

    def test_el_cuerpo_armado_a_mano_tiene_la_misma_forma(self):
        """Las vistas que responden sin lanzar excepción usan `cuerpo_de_error`."""
        self.assertEqual(
            cuerpo_de_error("Credenciales inválidas.", "credenciales_invalidas"),
            {
                "detail": "Credenciales inválidas.",
                "errores": [{
                    "codigo": "credenciales_invalidas",
                    "mensaje": "Credenciales inválidas.",
                }],
            },
        )

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
        self.assertEqual(r.data["errores"], [{
            "codigo": "error_pasarela",
            "mensaje": almacenamiento.MENSAJE_ALMACENAMIENTO,
        }])

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


OBLIGATORIO = str(serializers.Field.default_error_messages["required"])


class _Linea(EstructuraEstricta, serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    valor = serializers.DecimalField(max_digits=10, decimal_places=2)


class _Documento(EstructuraEstricta, serializers.Serializer):
    numero = serializers.CharField()
    lineas = _Linea(many=True)


class EstructuraEstrictaTests(SimpleTestCase):
    """Lo que no es un campo escribible se rechaza, en cualquier nivel."""

    def _errores(self, datos):
        serializer = _Documento(data=datos)
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_lo_correcto_pasa(self):
        serializer = _Documento(data={"numero": "1", "lineas": [{"valor": "1.00"}]})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_un_campo_desconocido_se_rechaza(self):
        errores = self._errores(
            {"numero": "1", "lineas": [{"valor": "1.00"}], "numro": "1"}
        )
        self.assertEqual(errores, {"numro": [MENSAJE_CAMPO_DESCONOCIDO]})

    def test_un_campo_de_solo_lectura_se_rechaza_dentro_de_un_anidado(self):
        """El error queda colgado del campo y de la posición que lo traía."""
        errores = self._errores(
            {"numero": "1", "lineas": [{"valor": "1.00"}, {"id": 5, "valor": "2.00"}]}
        )
        self.assertEqual(
            errores, {"lineas": [{}, {"id": [MENSAJE_CAMPO_SOLO_LECTURA]}]}
        )

    def test_lo_que_sobra_y_lo_que_falta_salen_juntos(self):
        """`numro` sobra y `numero` falta: es el mismo error de lectura."""
        errores = self._errores({"lineas": [{"valor": "1.00"}], "numro": "1"})
        self.assertEqual(
            errores,
            {"numro": [MENSAJE_CAMPO_DESCONOCIDO], "numero": [OBLIGATORIO]},
        )

    def test_la_estructura_frena_los_datos_de_cualquier_nivel(self):
        """El anidado se valida en medio de los campos del padre; su error de
        estructura tiene que verse antes, y el `valor` inválido no sale."""
        errores = self._errores(
            {"numero": "1", "lineas": [{"id": 5, "valor": "no es un número"}]}
        )
        self.assertEqual(errores, {"lineas": [{"id": [MENSAJE_CAMPO_SOLO_LECTURA]}]})

    def test_falta_un_obligatorio_dentro_de_un_anidado(self):
        errores = self._errores({"numero": "1", "lineas": [{"valor": "1.00"}, {}]})
        self.assertEqual(errores, {"lineas": [{}, {"valor": [OBLIGATORIO]}]})

    def test_un_valor_vacio_no_es_un_error_de_estructura(self):
        """La clave está: que venga nula es un error del dato, y sale después."""
        serializer = _Documento(data={"numero": None, "lineas": []})
        self.assertEqual(serializer.errores_de_estructura(serializer.initial_data), {})
        self.assertFalse(serializer.is_valid())
        self.assertIn("numero", serializer.errors)

    def test_con_la_estructura_bien_salen_los_errores_de_datos(self):
        errores = self._errores({"numero": "1", "lineas": [{"valor": "no es un número"}]})
        self.assertEqual(set(errores), {"lineas"})
        self.assertIn("valor", errores["lineas"][0])

    def test_lo_que_no_tiene_la_forma_esperada_no_se_recorre(self):
        """Un objeto donde va una lista no es un sobrante: es un error de tipo."""
        errores = self._errores({"numero": "1", "lineas": {"id": 5}})
        self.assertIn("lineas", errores)
        self.assertNotEqual(errores["lineas"], [MENSAJE_CAMPO_SOLO_LECTURA])

    def test_cada_error_de_estructura_lleva_su_codigo(self):
        """Es lo que el cliente mapea: el texto puede cambiar, el código no."""
        errores = self._errores({"numro": "1", "lineas": [{"id": 5, "valor": "1.00"}]})
        self.assertEqual(errores["numro"][0].code, CODIGO_CAMPO_DESCONOCIDO)
        self.assertEqual(errores["numero"][0].code, CODIGO_OBLIGATORIO)
        self.assertEqual(errores["lineas"][0]["id"][0].code, CODIGO_CAMPO_SOLO_LECTURA)

    def test_lo_que_no_es_un_objeto_da_el_error_de_siempre(self):
        errores = self._errores("texto")
        self.assertIn("non_field_errors", errores)
