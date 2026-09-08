"""Verificación del correo de quien se registra.

El registro es público, así que el correo hay que confirmarlo. Conviene tener
claro **qué protege y qué no**: no protege nada fiscal —para emitir hace falta
el `.p12` del NIT, que nadie puede falsificar—, sino que asegura que existe una
vía para avisarle a quien depende del servicio (un certificado por vencer, una
resolución sin consecutivos, un mantenimiento) y que la recuperación de
contraseña tiene a dónde ir.

El token va **firmado, no guardado**: ``TimestampSigner`` mete la marca de
tiempo dentro del propio token, así que no hace falta ni tabla ni limpieza de
tokens caducados, y un volcado de la base no revela ninguno. El precio es que
no se puede revocar uno suelto antes de que expire; a cambio, cambiar el correo
invalida los suyos, porque el correo va firmado dentro (ver ``usuario_de_token``).
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing

from apps.utilidades.zinc import Zinc, ZincError

logger = logging.getLogger(__name__)

Usuario = get_user_model()

# La sal ata el token a este uso: uno de verificación no vale para nada más
# aunque se firme con la misma SECRET_KEY.
SAL = "seguridad.verificacion-correo"

# Tres días: suficiente para quien se registra un viernes por la tarde, y no
# tanto como para que un enlace olvidado en una bandeja siga sirviendo.
VIGENCIA_SEGUNDOS = 60 * 60 * 24 * 3

APLICACION = "nobelio"


class TokenInvalido(Exception):
    """El token no es legible, está caducado o ya no corresponde al usuario."""


def generar_token(usuario) -> str:
    """Token firmado que identifica a ``usuario`` para verificar su correo."""
    return signing.dumps({"uid": usuario.pk, "email": usuario.email}, salt=SAL)


def usuario_de_token(token: str):
    """Devuelve el ``Usuario`` del token, o lanza :class:`TokenInvalido`.

    Comprueba también que el correo firmado siga siendo el del usuario: si lo
    cambió después de pedir el enlace, el viejo deja de servir. Sin eso, un
    enlace emitido para una dirección seguiría verificando la cuenta después de
    apuntarla a otra.
    """
    try:
        datos = signing.loads(token, salt=SAL, max_age=VIGENCIA_SEGUNDOS)
    except signing.SignatureExpired:
        raise TokenInvalido("El enlace de verificación caducó. Pide uno nuevo.")
    except signing.BadSignature:
        raise TokenInvalido("El enlace de verificación no es válido.")

    try:
        usuario = Usuario.objects.get(pk=datos.get("uid"))
    except Usuario.DoesNotExist:
        raise TokenInvalido("El enlace de verificación no es válido.")

    if usuario.email != datos.get("email"):
        raise TokenInvalido(
            "El enlace ya no es válido porque el correo de la cuenta cambió."
        )
    return usuario


def enlace_de(token: str) -> str:
    """URL del sitio a la que apunta el correo, con el token colgando."""
    base = settings.URL_VERIFICACION_CORREO.rstrip("/")
    separador = "&" if "?" in base else "?"
    return f"{base}{separador}token={token}"


def cuerpo_html(usuario, enlace: str) -> str:
    """Cuerpo del correo de verificación."""
    saludo = usuario.get_short_name()
    return (
        f"<p>Hola, {saludo}.</p>"
        f"<p>Para terminar de crear tu cuenta confirma este correo:</p>"
        f'<p><a href="{enlace}">Confirmar mi correo</a></p>'
        f"<p>Si el enlace no funciona, copia esta dirección en el navegador:<br>"
        f"{enlace}</p>"
        f"<p>El enlace vence en 3 días. "
        f"Si no fuiste tú quien se registró, ignora este mensaje.</p>"
    )


def payload_zinc(usuario, enlace: str) -> dict:
    """Arma el cuerpo que espera Zinc en ``/api/correo/html``.

    Aislado igual que el de las notificaciones de documentos: el contrato lo
    define la pasarela, así que si cambian los nombres se corrige aquí.
    """
    return {
        "correo": usuario.email,
        "asunto": "Confirma tu correo",
        "contenido": cuerpo_html(usuario, enlace),
        "nombreRemitente": getattr(settings, "ZINC_NOMBRE_REMITENTE", APLICACION),
        "aplicacion": APLICACION,
    }


def enviar_verificacion(usuario, *, zinc=None) -> bool:
    """Envía el correo de verificación. Devuelve si salió.

    **No propaga el fallo a propósito.** El alta ya está confirmada en la base
    cuando esto se llama, y tumbar la respuesta porque la pasarela tuvo un mal
    momento dejaría al usuario creado y sin saberlo. Se responde que la cuenta
    existe y que el correo no salió, y quien se registró pide el reenvío.

    El cliente se puede inyectar para las pruebas.
    """
    enlace = enlace_de(generar_token(usuario))
    cliente = zinc or Zinc()
    try:
        respuesta = cliente.correo_html(payload_zinc(usuario, enlace))
    except ZincError:
        logger.exception("No se pudo enviar la verificación a %s", usuario.email)
        return False
    if respuesta.get("error"):
        logger.error(
            "Zinc rechazó la verificación de %s: %s", usuario.email, respuesta
        )
        return False
    return True
