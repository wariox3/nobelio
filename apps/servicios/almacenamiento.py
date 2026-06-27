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
