"""Que Sentry no se lleve un secreto.

Es la única parte de la integración que merece prueba: el filtro de nombres.
Sentry trae el suyo en inglés y aquí los secretos se llaman `clave`, `pin` y
`clave_tecnica`, así que si esta lista se queda corta, la clave del `.p12`
acaba en un traceback de producción sin que nadie se entere.

No hay red: se captura el evento con un transporte falso y se mira lo que
*habría* salido.
"""
from django.test import SimpleTestCase
from sentry_sdk.transport import Transport

from config import observabilidad


class TransporteFalso(Transport):
    """Recoge los sobres en vez de mandarlos a ninguna parte.

    Hereda de `Transport` porque el SDK comprueba el tipo: un objeto suelto con
    los mismos métodos no lo acepta.
    """

    def __init__(self):
        super().__init__()
        self.eventos = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.payload.json is not None:
                self.eventos.append(item.payload.json)

    def flush(self, *a, **k):
        pass

    def kill(self, *a, **k):
        pass


class FiltroDeSecretosTests(SimpleTestCase):
    def _capturar(self, funcion):
        """Ejecuta ``funcion``, deja que reviente y devuelve el evento resultante."""
        import sentry_sdk
        from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

        transporte = TransporteFalso()
        cliente = sentry_sdk.Client(
            dsn="https://sinclave@ejemplo.invalid/1",
            transport=transporte,
            include_local_variables=True,
            event_scrubber=EventScrubber(
                denylist=DEFAULT_DENYLIST + observabilidad.NOMBRES_SENSIBLES,
                recursive=True,
            ),
            auto_enabling_integrations=False,
            default_integrations=False,
        )
        # `new_scope` aísla el cliente falso: no toca el Sentry del proceso, que
        # en la suite además no existe (no hay DSN).
        with sentry_sdk.new_scope() as scope:
            scope.set_client(cliente)
            try:
                funcion()
            except Exception:
                sentry_sdk.capture_exception()
        cliente.flush()
        self.assertEqual(len(transporte.eventos), 1, "no se capturó el evento")
        return transporte.eventos[0]

    def _variables(self, evento):
        """Las variables locales del frame donde saltó la excepción.

        Se mira aquí y no en el JSON entero a propósito. Sentry envía además las
        **líneas de código fuente** de cada frame, así que un secreto escrito
        como literal en el código aparecería en el evento por mucho que el
        filtro borre la variable. En el código real el valor sale de la base de
        datos y el fuente solo enseña la expresión (`certificado.clave`), que es
        justo lo que se quiere ver.
        """
        frames = evento["exception"]["values"][0]["stacktrace"]["frames"]
        return frames[-1]["vars"]

    def test_la_clave_del_p12_no_viaja(self, secreto="la-clave-del-p12"):
        def firmar(valor):
            clave = valor                      # noqa: F841
            datos = valor.encode()             # noqa: F841
            raise ValueError("fallo al abrir el certificado")

        variables = self._variables(self._capturar(lambda: firmar(secreto)))
        self.assertEqual(variables["clave"], "[Filtered]")
        self.assertEqual(variables["datos"], "[Filtered]")

    def test_el_pin_del_software_no_viaja(self, secreto="pin-del-software"):
        def componer_cune(valor):
            pin = valor                        # noqa: F841
            clave_tecnica = valor              # noqa: F841
            raise ValueError("no se pudo componer el CUNE")

        variables = self._variables(self._capturar(lambda: componer_cune(secreto)))
        self.assertEqual(variables["pin"], "[Filtered]")
        self.assertEqual(variables["clave_tecnica"], "[Filtered]")

    def test_el_secreto_de_una_llave_api_no_viaja(self, valor="secreto-api-key"):
        def autenticar(v):
            secreto = v                        # noqa: F841
            clave_hash = v                     # noqa: F841
            raise ValueError("credencial inválida")

        variables = self._variables(self._capturar(lambda: autenticar(valor)))
        self.assertEqual(variables["secreto"], "[Filtered]")
        self.assertEqual(variables["clave_hash"], "[Filtered]")

    def test_lo_que_no_es_secreto_si_viaja(self):
        """El filtro tiene que dejar pasar lo que sirve para depurar.

        Sin esto, un filtro demasiado ancho pasaría las otras pruebas y dejaría
        los eventos inservibles.
        """
        def emitir(numero):
            numero_documento = numero          # noqa: F841
            raise ValueError("el documento no tiene resolución")

        variables = self._variables(
            self._capturar(lambda: emitir("SETP990000129"))
        )
        self.assertEqual(variables["numero_documento"], "'SETP990000129'")


class ConfiguracionTests(SimpleTestCase):
    def test_sin_dsn_no_se_inicializa(self):
        """En dev y en la suite Sentry no debe existir."""
        self.assertFalse(
            observabilidad.configurar(dsn="", entorno="pruebas", traces=0.0)
        )

    def test_los_nombres_en_espanol_estan_en_la_lista(self):
        """La lista de fábrica es en inglés; esto es lo que la completa."""
        for nombre in ("clave", "pin", "clave_tecnica", "secreto", "clave_hash"):
            self.assertIn(nombre, observabilidad.NOMBRES_SENSIBLES)
