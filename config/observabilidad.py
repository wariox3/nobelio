"""Integración con Sentry.

Se activa **solo si hay `SENTRY_DSN`**. Sin la variable no se inicializa nada,
así que en desarrollo y en la suite de pruebas Sentry no existe: no hay red, no
hay eventos y no hay que acordarse de apagarlo.

Qué llega y qué no
------------------
- **Eventos**: las excepciones no controladas y todo lo que se registre a nivel
  `ERROR`. Un rechazo de la DIAN **no** es un evento: es un problema de datos
  del cliente, no un fallo del sistema, y llenar Sentry de rechazos haría que
  las alertas dejaran de mirarse. Se quedan como migas de pan, que es donde
  aportan: al abrir un fallo se ve qué pasó justo antes.
- **Migas de pan**: las trazas de emisión de `apps.dian.servicios` (`INFO`), que
  ya están pensadas para no llevar datos de terceros —cuentan destinatarios, no
  los nombran—.
- **Trazas de rendimiento**: apagadas por defecto. Lo único que se querría medir
  hoy es cuánto tarda un envío a la DIAN (el punto D3 de
  `docs/revision-tecnica.md`), y eso ya sale del logging. Se enciende subiendo
  `SENTRY_TRACES` sin tocar código.

El filtro
---------
Esta es la parte que hay que mantener. Sentry trae una lista de nombres
sensibles que borra de los eventos, pero está **en inglés**: `password`,
`secret`, `token`… Aquí los secretos se llaman `clave`, `pin` y
`clave_tecnica`, así que con la lista de fábrica viajarían enteros —y con las
variables locales activadas, la clave del `.p12` aparecería en cualquier
traceback del pipeline de firma—.

`NOMBRES_SENSIBLES` los añade. **Al crear un campo o una variable que lleve un
secreto, hay que apuntarlo ahí.** Es una lista que se queda corta sola.

Lo que el filtro **no** puede tapar
-----------------------------------
Sentry envía también las líneas de código fuente alrededor de cada frame. El
filtro borra el *valor* de una variable, no el *texto* del programa: un secreto
escrito como literal en el código viajaría igual. No es un agujero nuevo —un
secreto en el fuente ya está en el repositorio—, pero conviene saberlo antes de
«poner una clave un momento para probar».

Lo protege `config/tests_observabilidad.py`.
"""
import logging

NOMBRES_SENSIBLES = [
    # Material de firma
    "clave",                 # Certificado.clave: la contraseña del .p12
    "clave_hash",            # LlaveApi.clave_hash
    "clave_tecnica",         # Resolucion.clave_tecnica
    "pin",                   # SoftwareDian.pin: entra en el CUDE y en el CUNE
    "secreto",               # el secreto de una API Key, antes de hashearlo
    "CERT_ENCRYPTION_KEY",   # la clave que cifra las claves
    # Contenido que no es un secreto pero sí datos fiscales de terceros, o
    # binario que no aporta nada en un traceback y sí lo engorda.
    "datos",                 # los bytes del .p12 en validar_pkcs12
    "p12",
    "llave",                 # la llave privada cargada
    "xml_firmado",
    "sobre",                 # el sobre SOAP: lleva el documento y el certificado
]


def configurar(*, dsn, entorno, traces, release=""):
    """Arranca Sentry. Sin ``dsn`` no hace nada y lo dice devolviendo ``False``."""
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    sentry_sdk.init(
        dsn=dsn,
        environment=entorno,
        release=release or None,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(
                level=logging.INFO,          # de INFO para arriba, migas de pan
                event_level=logging.ERROR,   # de ERROR para arriba, eventos
            ),
        ],
        traces_sample_rate=traces,
        # Nunca los datos personales que Sentry adjunta por su cuenta (IP del
        # cliente, cabeceras de identidad). Es el valor por defecto del SDK y se
        # escribe explícito porque aquí importa: por esta API pasan datos
        # fiscales de terceros, no solo de quien integra.
        send_default_pii=False,
        # Con las variables locales se ve *con qué datos* falló, que es la mitad
        # del valor de un traceback. El precio es que hay que mantener bien
        # `NOMBRES_SENSIBLES`.
        include_local_variables=True,
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST + NOMBRES_SENSIBLES,
            recursive=True,
        ),
    )
    return True
