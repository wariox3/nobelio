"""Acceso al bucket R2 donde el Email Worker guarda el MIME crudo.

R2 habla la API de S3: se usa boto3 contra el endpoint de la cuenta, con la
región ``auto`` que pide Cloudflare. Las credenciales son las ``R2_*``.
"""
from functools import lru_cache

from django.conf import settings


class R2NoConfigurado(Exception):
    """Faltan las variables ``R2_*``."""


@lru_cache(maxsize=1)
def _cliente():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def borrar_mime(raw_key):
    """Borra el MIME de ``raw_key``.

    Borrar una clave que ya no existe no es error en S3, así que repetirlo es
    seguro. Lanza ``R2NoConfigurado`` sin credenciales, y los errores de
    botocore tal cual.
    """
    if not settings.R2_HABILITADO:
        raise R2NoConfigurado
    _cliente().delete_object(Bucket=settings.R2_BUCKET, Key=raw_key)
