"""Almacenamiento de archivos en Backblaze B2 (API S3-compatible).

B2 alojará varios tipos de archivo del sistema; por ahora los certificados
``.p12``. Se usa ``django-storages`` (backend S3) contra el endpoint
S3-compatible de B2.

Si las credenciales B2 no están configuradas (entorno de desarrollo o pruebas),
se cae al almacenamiento local por defecto, de modo que el proyecto funciona sin
depender de la nube. Las credenciales viven en variables de entorno
(``B2_*``), nunca en el código.
"""
from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.core.files.storage import Storage, default_storage

# Un único mensaje para cualquier fallo del almacenamiento: el detalle (código
# de botocore, keyID, bucket) va al log del servidor, no a la respuesta HTTP.
MENSAJE_ALMACENAMIENTO = "No se pudo acceder al almacenamiento."


@lru_cache(maxsize=1)
def _storage_b2() -> Storage:
    """Construye (una sola vez) el storage S3 apuntando a Backblaze B2."""
    from storages.backends.s3 import S3Storage

    return S3Storage(
        bucket_name=settings.B2_BUCKET,
        endpoint_url=settings.B2_ENDPOINT_URL,
        region_name=settings.B2_REGION,
        access_key=settings.B2_KEY_ID,
        secret_key=settings.B2_APP_KEY,
        # Bucket privado: nada público; las URLs se firman con expiración.
        default_acl=None,
        querystring_auth=True,
        # No sobrescribir: si ya existe un archivo con el mismo nombre, B2 le
        # añade un sufijo (igual que el almacenamiento local).
        file_overwrite=False,
    )


def almacenamiento_backblaze() -> Storage:
    """Devuelve el storage de B2 si está configurado; si no, el local.

    Pensado para usarse como ``storage=`` de un ``FileField``. Distintos campos
    de archivo pueden compartir este mismo almacenamiento.
    """
    if settings.B2_HABILITADO:
        return _storage_b2()
    return default_storage


def motivo_error_almacenamiento(exc) -> str | None:
    """Mensaje para la API si ``exc`` es un fallo del almacenamiento en la nube.

    Devuelve ``None`` cuando la excepción no viene de botocore, para que el
    resto de errores sigan su curso normal.
    """
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:  # boto3 no instalado: nada que traducir.
        return None

    # ClientError (respuesta de error del servicio) y BotoCoreError (conexión,
    # DNS, timeout) son ramas distintas: hacen falta las dos.
    if isinstance(exc, (BotoCoreError, ClientError)):
        return MENSAJE_ALMACENAMIENTO
    return None
