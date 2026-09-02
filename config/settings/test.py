"""Settings para la suite de pruebas.

Desactiva Backblaze B2 para que el almacenamiento de archivos (XML, etc.) caiga
a ``FileSystemStorage`` local en un directorio temporal: rápido, sin red y sin
ensuciar el bucket real.
"""
import tempfile

from .base import *  # noqa: F401,F403

# Sin credenciales B2 -> almacenamiento_backblaze() usa el storage local.
B2_BUCKET = ""
B2_ENDPOINT_URL = ""
B2_KEY_ID = ""
B2_APP_KEY = ""
B2_HABILITADO = False

# Archivos de prueba en un temporal aislado (no en el repo).
MEDIA_ROOT = tempfile.mkdtemp(prefix="nobelio-test-media-")

# Clave de cifrado fija para la suite: la de verdad es obligatoria y sale del
# .env, y las pruebas no deben depender de que exista ni tocar el material real.
# Es fija y no generada para que un fallo se reproduzca igual en cada ejecución.
CERT_ENCRYPTION_KEY = "SsHkSDR23bZuoCyxvHOEipYbGrCJJhcThPCanEwLHi4="

# Las trazas de emisión, calladas durante la suite: son útiles en el servidor,
# pero aquí solo ensucian la salida y esconden el fallo que se está buscando.
# Se suben a INFO puntualmente con `assertLogs` cuando lo que se prueba es
# justamente que la línea se escribe.
LOGGING["loggers"]["apps"]["level"] = "CRITICAL"  # noqa: F405

# Y los 400/500 que Django registra en `django.request`: en la suite hay
# pruebas que provocan errores a propósito, así que su traza es ruido que
# confunde al leer un fallo de verdad.
LOGGING["loggers"]["django.request"]["level"] = "CRITICAL"  # noqa: F405

# Sin límite de peticiones en la suite: el contador vive en la caché y se
# acumula entre pruebas del mismo proceso, así que una clase con muchos casos
# empezaría a recibir 429 por el orden en que se ejecutan, no por lo que prueba.
# El límite se comprueba en su propia prueba, que fija la tasa a mano.
# Las tasas se declaran pero en `None`: DRF exige que el scope exista y trata
# el `None` como "sin límite".
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"user": None, "anon": None},
}
