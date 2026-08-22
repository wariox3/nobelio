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
