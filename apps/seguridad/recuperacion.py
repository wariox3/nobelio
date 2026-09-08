"""Recuperación de la contraseña.

Mismo mecanismo que la verificación del correo —token firmado, sin tabla— con
una diferencia que importa: **el enlace se quema al usarse**. Un token de
verificación que sirva dos veces no hace daño; uno de recuperación que siga
válido después de cambiar la clave es una segunda llave de la cuenta.

Eso se consigue sin guardar nada: dentro de la firma va una huella del hash de
la contraseña actual. Al cambiarla, la huella deja de coincidir y todos los
enlaces emitidos antes dejan de validar solos. Es la idea de
``PasswordResetTokenGenerator`` de Django, adaptada al formato del proyecto.

Lo que este flujo **no** hace es saltarse el segundo factor: quien restablece su
contraseña sigue teniendo que pasar por el segundo paso al iniciar sesión. Si no
fuera así, el acceso al correo se convertiría en la llave maestra y tener MFA no
significaría nada.
"""
import hashlib
import hmac
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing

from apps.utilidades.zinc import Zinc, ZincError

logger = logging.getLogger(__name__)

Usuario = get_user_model()

SAL = "seguridad.recuperacion-clave"

# Una hora, no los tres días de la verificación: aquí el enlace abre la cuenta,
# así que cuanto menos viva, mejor. Sigue siendo tiempo de sobra para abrir el
# correo y escribir una contraseña.
VIGENCIA_SEGUNDOS = 60 * 60

APLICACION = "nobelio"


class TokenInvalido(Exception):
    """El enlace no es legible, caducó o ya se usó."""


def _huella(usuario) -> str:
    """Huella del hash de la contraseña actual.

    No viaja el hash: viaja su HMAC recortado, que basta para detectar el cambio
    y no filtra material con el que atacar la contraseña si alguien ve el token.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(), usuario.password.encode(), hashlib.sha256
    ).hexdigest()[:16]


def generar_token(usuario) -> str:
    return signing.dumps(
        {"uid": usuario.pk, "huella": _huella(usuario)}, salt=SAL
    )


def usuario_de_token(token: str):
    """Devuelve el ``Usuario`` del token, o lanza :class:`TokenInvalido`.

    Los tres motivos de rechazo dan el mismo mensaje a propósito: distinguir
    "caducó" de "ya se usó" le diría a quien no debe que el enlace existió.
    """
    try:
        datos = signing.loads(token, salt=SAL, max_age=VIGENCIA_SEGUNDOS)
    except signing.BadSignature:
        raise TokenInvalido(
            "El enlace no es válido o ya caducó. Pide uno nuevo."
        )

    usuario = Usuario.objects.filter(pk=datos.get("uid")).first()
    if usuario is None or not hmac.compare_digest(
        _huella(usuario), datos.get("huella", "")
    ):
        raise TokenInvalido(
            "El enlace no es válido o ya caducó. Pide uno nuevo."
        )
    return usuario


def enlace_de(token: str) -> str:
    base = settings.URL_RESTABLECER_CLAVE.rstrip("/")
    separador = "&" if "?" in base else "?"
    return f"{base}{separador}token={token}"


def cuerpo_html(usuario, enlace: str) -> str:
    minutos = VIGENCIA_SEGUNDOS // 60
    return (
        f"<p>Hola, {usuario.get_short_name()}.</p>"
        "<p>Pediste restablecer tu contraseña. Este enlace te lleva a hacerlo:</p>"
        f'<p><a href="{enlace}">Cambiar mi contraseña</a></p>'
        "<p>Si el enlace no funciona, copia esta dirección en el navegador:<br>"
        f"{enlace}</p>"
        f"<p>Vence en {minutos} minutos y solo se puede usar una vez.</p>"
        "<p>Si no fuiste tú, ignora este mensaje: tu contraseña no ha cambiado.</p>"
    )


def enviar_recuperacion(usuario, *, zinc=None) -> bool:
    """Manda el enlace. Devuelve si salió; no propaga los fallos de la pasarela."""
    enlace = enlace_de(generar_token(usuario))
    cliente = zinc or Zinc()
    try:
        respuesta = cliente.correo_html({
            "correo": usuario.email,
            "asunto": "Restablecer tu contraseña",
            "contenido": cuerpo_html(usuario, enlace),
            "nombreRemitente": getattr(
                settings, "ZINC_NOMBRE_REMITENTE", APLICACION
            ),
            "aplicacion": APLICACION,
        })
    except ZincError:
        logger.exception("No se pudo enviar la recuperación a %s", usuario.email)
        return False
    if respuesta.get("error"):
        logger.error("Zinc rechazó la recuperación de %s", usuario.email)
        return False
    return True
