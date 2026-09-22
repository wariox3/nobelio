
"""Cifrado de los secretos que se guardan en la base.

Lo usan la clave del `.p12` del emisor (`Certificado.clave`), que hasta el
2026-09-02 se guardaba en claro, y el secreto de los webhooks
(`Webhook.secreto`), cada uno con su propia clave Fernet. El motivo del cambio es que las dos mitades
del material de firma vivían en el mismo servidor: un volcado de la base daba
la clave, y el `.env` de al lado daba las credenciales B2 con las que bajar el
`.p12`. Con las dos juntas se firma cualquier documento en nombre de cualquier
emisor de la plataforma. Cifrar la columna con una clave que no está en la base
rompe esa pareja: el volcado por sí solo ya no sirve de nada.

Se usa Fernet (AES-128-CBC + HMAC-SHA256, de `cryptography`, que ya era
dependencia por la firma XAdES). El token lleva su propio IV y su marca de
integridad, así que un valor manipulado a mano en la base no descifra en
silencio: falla.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

# La de los certificados, que fue la primera. Cada dominio de secretos tiene la
# suya para que rotar una no deje ilegibles los de los otros.
LLAVE_POR_DEFECTO = "CERT_ENCRYPTION_KEY"


@lru_cache(maxsize=None)
def _cifrador_de(clave: str) -> Fernet:
    """El ``Fernet`` de una clave, construido una sola vez.

    Se cachea por clave y no en un global para que ``override_settings`` en las
    pruebas siga funcionando: al cambiar la clave cambia la entrada de la caché.
    """
    return Fernet(clave)


def cifrador(llave: str = LLAVE_POR_DEFECTO) -> Fernet:
    """El ``Fernet`` de la clave que está en el setting ``llave``.

    La de los certificados es obligatoria y ya la exige el arranque; las demás
    pueden estar vacías, y entonces se falla aquí, al usarla, nombrando cuál.
    """
    clave = getattr(settings, llave, "")
    if not clave:
        raise ImproperlyConfigured(f"{llave} no está configurada.")
    return _cifrador_de(clave)


def cifrar(valor: str, llave: str = LLAVE_POR_DEFECTO) -> str:
    """Devuelve el token Fernet de ``valor``, en texto para guardarlo."""
    return cifrador(llave).encrypt(valor.encode()).decode()


def descifrar(valor: str, llave: str = LLAVE_POR_DEFECTO) -> str:
    """Devuelve el texto en claro de un token Fernet.

    Tolera que ``valor`` no sea un token: en ese caso lo devuelve tal cual. Eso
    cubre las filas que todavía estuvieran en claro —las anteriores a la
    migración de datos, o las que escriba algo que no pase por el campo— para
    que un emisor no se quede sin poder firmar por un dato sin migrar. La
    escritura siempre cifra, así que estas filas se van agotando solas.
    """
    try:
        return cifrador(llave).decrypt(valor.encode()).decode()
    except InvalidToken:
        return valor


class ClaveCifradaField(models.CharField):
    """``CharField`` que se guarda cifrado y se lee en claro.

    El cifrado es transparente: en Python el atributo vale siempre lo que se le
    puso, y lo que viaja a la base es el token. Así los sitios que usan la clave
    para abrir el `.p12` (``apps.dian.servicios``) no tuvieron que cambiar.

    Lo que no se puede es buscar por este campo: cada cifrado usa un IV nuevo,
    así que el mismo texto da tokens distintos y un ``filter(clave=...)`` no
    encontraría nada. No hay ningún sitio que lo haga, y no tendría sentido que
    lo hubiera.

    ``llave`` es el nombre del setting con la clave Fernet; por defecto, la de
    los certificados.
    """

    def __init__(self, *args, llave=LLAVE_POR_DEFECTO, **kwargs):
        self.llave = llave
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        nombre, ruta, args, kwargs = super().deconstruct()
        if self.llave != LLAVE_POR_DEFECTO:
            kwargs["llave"] = self.llave
        return nombre, ruta, args, kwargs

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return descifrar(value, self.llave)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return cifrar(value, self.llave)
