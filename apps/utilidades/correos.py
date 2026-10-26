"""Listas de correos en un solo campo.

Los ERP mandan varios correos del adquiriente en el mismo campo, separados por
``;``. Aquí se parten, se validan uno por uno y se vuelven a unir, para que el
resto del sistema no repita esa lógica.

Se guardan separados por ``;`` (como llegan) y a la pasarela Zinc se le envían
separados por comas, que es como ella los acepta.
"""
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

SEPARADOR = ";"
SEPARADOR_ZINC = ","


def separar_correos(valor):
    """Los correos del campo, sin espacios ni vacíos."""
    return [c.strip() for c in (valor or "").split(SEPARADOR) if c.strip()]


def unir_correos(correos, separador=SEPARADOR):
    return separador.join(correos)


def normalizar_correos(valor):
    """El campo limpio: ``" a@b.co ; c@d.co;"`` → ``"a@b.co;c@d.co"``."""
    return unir_correos(separar_correos(valor))


def primer_correo(valor):
    """El primero de la lista, o ``""``. Es el que va en el XML."""
    correos = separar_correos(valor)
    return correos[0] if correos else ""


def validar_correos(valor):
    """Valida cada correo de la lista y nombra los que no sirven."""
    invalidos = []
    for correo in separar_correos(valor):
        try:
            validate_email(correo)
        except ValidationError:
            invalidos.append(correo)
    if invalidos:
        raise ValidationError(
            "Correo electrónico no válido: %(correos)s. Varios correos van "
            "separados por punto y coma (;).",
            code="invalid",
            params={"correos": ", ".join(invalidos)},
        )
