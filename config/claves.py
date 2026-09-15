"""Comprobación de las claves Fernet al arrancar.

Vive fuera de las apps porque la llaman los settings, que se cargan antes que
nada: no puede depender de Django más allá de sus excepciones.
"""
from django.core.exceptions import ImproperlyConfigured

COMANDO_GENERAR = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


def clave_fernet(nombre, valor, *, obligatoria=True):
    """Devuelve ``valor`` si es una clave Fernet válida; si no, no deja arrancar.

    Antes solo se comprobaba que la variable existiera. Una clave mal formada
    —la de ``secrets.token_urlsafe``, que es el comando de la
    ``DJANGO_SECRET_KEY`` y está justo al lado en ``docs/despliegue.md``, o una
    copiada sin el ``=`` final— dejaba arrancar el servidor y reventaba en
    producción la primera vez que se cargaba o se leía un certificado, con el
    ``.p12`` ya subido a B2. Pasó el 2026-09-15.

    El mensaje nombra la variable y dice cómo generarla, pero nunca incluye el
    valor: los errores de arranque acaban en logs y en Sentry.
    """
    if not valor:
        if obligatoria:
            raise ImproperlyConfigured(
                f"Falta {nombre}. Genérela con: {COMANDO_GENERAR}"
            )
        return valor

    from cryptography.fernet import Fernet

    try:
        Fernet(valor)
    except (ValueError, TypeError):
        # `from None`: la excepción de `cryptography` no aporta nada que no diga
        # ya el mensaje, y así el traceback de arranque queda en una línea.
        raise ImproperlyConfigured(
            f"{nombre} no es una clave Fernet válida: tienen que ser 44 "
            f"caracteres en base64 url-safe, terminados en '='. Genérela con: "
            f"{COMANDO_GENERAR}"
        ) from None
    return valor
