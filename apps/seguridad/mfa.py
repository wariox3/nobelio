"""Motor del segundo factor: secretos, códigos, desafíos y dispositivos.

Las vistas no deberían saber de ``pyotp``, de Fernet ni de cómo se hashea un
código: aquí se concentra esa mecánica y ellas solo traducen ``ErrorMfa`` en una
respuesta HTTP.

Dos decisiones gobiernan el resto:

- **Los intentos se cuentan en la base, no en la caché.** Es el único punto donde
  se puede probar un código de seis dígitos, y un contador por worker que se
  reinicia en cada despliegue no es un cerrojo. La caché modera el tráfico; esto
  frena la fuerza bruta.
- **Nada se guarda en claro.** El secreto TOTP va cifrado y todo lo demás como
  HMAC con clave, no SHA-256 pelado: un código de seis dígitos tiene un millón de
  posibilidades y un hash sin clave se revierte al instante desde un volcado.
"""
import hashlib
import hmac
import logging
import secrets
import time
from datetime import timedelta
from typing import NamedTuple

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.seguridad.models import (
    METODO_CORREO,
    METODOS_ENVIADOS,
    MfaCodigoRespaldo,
    MfaDesafio,
    MfaDispositivo,
    MfaUsuario,
)
from apps.utilidades.zinc import Zinc, ZincError

logger = logging.getLogger(__name__)

# Nombre que ve la persona en su app autenticadora.
EMISOR = "Nobelio"

# Cinco minutos: alcanza para abrir el correo, y una ventana olvidada deja de
# servir pronto.
DURACION_DESAFIO = timedelta(minutes=5)
MAX_INTENTOS = 5

# ±1 ventana de 30 s, para tolerar el desfase de reloj de los celulares.
VENTANA_TOTP = 1
PERIODO_TOTP = 30

LONGITUD_CODIGO = 6

CANTIDAD_RESPALDO = 10
LONGITUD_RESPALDO = 10
# Base32 sin 0/1/8/9: 32 símbolos, 10 caracteres ≈ 50 bits, y nada que confundir
# al copiarlo a mano.
ALFABETO_RESPALDO = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

DURACION_DISPOSITIVO = timedelta(days=30)

_SAL_DESAFIO = "seguridad.mfa-desafio"
_SAL_DISPOSITIVO = "seguridad.mfa-dispositivo"


class ErrorMfa(Exception):
    """Error con el mensaje ya redactado para quien lo va a leer.

    Las vistas lo devuelven tal cual: por eso ningún mensaje distingue entre
    "código incorrecto" y "desafío de otra cuenta".
    """


# --------------------------------------------------------------------------- #
# Cifrado y hash
# --------------------------------------------------------------------------- #

def _fernet():
    if not settings.MFA_ENCRYPTION_KEY:
        raise ImproperlyConfigured(
            "MFA_ENCRYPTION_KEY no está configurada. Generarla con: python -c "
            '"from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.MFA_ENCRYPTION_KEY.encode())


def cifrar_secreto(secreto: str) -> str:
    return _fernet().encrypt(secreto.encode()).decode()


def descifrar_secreto(cifrado: str) -> str:
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken:
        # Pasa si se rotó la llave sin migrar los secretos. No es culpa de quien
        # entra, pero para él el efecto es el mismo: su app dejó de servir.
        raise ErrorMfa(
            "No se pudo leer la configuración de tu segundo factor. "
            "Contacta a soporte."
        )


def _hash(valor: str) -> str:
    """HMAC-SHA256 con la llave del MFA, no SHA-256 pelado (ver el módulo)."""
    clave = (settings.MFA_ENCRYPTION_KEY or settings.SECRET_KEY).encode()
    return hmac.new(clave, valor.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# TOTP
# --------------------------------------------------------------------------- #

def generar_secreto() -> str:
    return pyotp.random_base32()


def uri_otpauth(usuario, secreto: str) -> str:
    """URI ``otpauth://`` que el front convierte en QR.

    El backend no genera la imagen: menos dependencias, y el secreto no viaja
    como PNG por ahí.
    """
    return pyotp.TOTP(secreto).provisioning_uri(
        name=usuario.email, issuer_name=EMISOR
    )


def _verificar_totp(mfa: MfaUsuario, codigo: str) -> bool:
    """Valida el código y consume la ventana que lo generó.

    ``pyotp.verify(valid_window=1)`` no dice cuál de las tres ventanas coincidió,
    y sin ese dato no se puede impedir el replay: quien intercepte el código
    podría reusarlo durante los 90 s en que sigue siendo válido. Por eso se
    recorren a mano y se guarda el contador.
    """
    if not mfa.secreto:
        return False

    totp = pyotp.TOTP(descifrar_secreto(mfa.secreto))
    actual = int(time.time()) // PERIODO_TOTP

    for delta in range(-VENTANA_TOTP, VENTANA_TOTP + 1):
        contador = actual + delta
        if not hmac.compare_digest(totp.generate_otp(contador), codigo):
            continue
        if mfa.ultimo_contador is not None and contador <= mfa.ultimo_contador:
            return False
        mfa.ultimo_contador = contador
        mfa.save(update_fields=["ultimo_contador", "actualizado_en"])
        return True

    return False


# --------------------------------------------------------------------------- #
# Códigos
# --------------------------------------------------------------------------- #

def generar_codigo() -> str:
    return f"{secrets.randbelow(10 ** LONGITUD_CODIGO):0{LONGITUD_CODIGO}d}"


def _normalizar_respaldo(codigo: str) -> str:
    return codigo.strip().upper().replace("-", "").replace(" ", "")


def generar_codigos_respaldo(usuario) -> list[str]:
    """Reemplaza los anteriores y devuelve los nuevos en claro.

    Es la única vez que existen legibles: en la base solo queda el HMAC.
    """
    codigos = [
        "".join(secrets.choice(ALFABETO_RESPALDO) for _ in range(LONGITUD_RESPALDO))
        for _ in range(CANTIDAD_RESPALDO)
    ]
    with transaction.atomic():
        MfaCodigoRespaldo.objects.filter(usuario=usuario).delete()
        MfaCodigoRespaldo.objects.bulk_create([
            MfaCodigoRespaldo(usuario=usuario, hash_codigo=_hash(c)) for c in codigos
        ])
    return codigos


def _consumir_respaldo(usuario, codigo: str) -> bool:
    consumidos = MfaCodigoRespaldo.objects.filter(
        usuario=usuario,
        hash_codigo=_hash(_normalizar_respaldo(codigo)),
        usado_en__isnull=True,
    ).update(usado_en=timezone.now())
    return consumidos > 0


def respaldos_restantes(usuario) -> int:
    return MfaCodigoRespaldo.objects.filter(
        usuario=usuario, usado_en__isnull=True
    ).count()


# --------------------------------------------------------------------------- #
# Desafíos
# --------------------------------------------------------------------------- #

def crear_desafio(usuario, metodo: str, ip: str = None):
    """Abre el segundo paso de un ingreso cuya clave ya se validó.

    Devuelve ``(desafio, codigo)``. En TOTP el código es ``None``: no se guarda
    ni se transmite, se recalcula del secreto.
    """
    MfaDesafio.objects.filter(expira__lt=timezone.now()).delete()

    codigo = generar_codigo() if metodo in METODOS_ENVIADOS else None
    desafio = MfaDesafio.objects.create(
        usuario=usuario,
        metodo=metodo,
        hash_codigo=_hash(codigo) if codigo else "",
        expira=timezone.now() + DURACION_DESAFIO,
        ip=ip,
    )
    return desafio, codigo


def firmar_desafio(desafio: MfaDesafio) -> str:
    """Token opaco que viaja al cliente entre los dos pasos.

    Es el id firmado: la firma impide enumerar desafíos ajenos, y el consumo
    único y el conteo de intentos los da la fila en la base.
    """
    return signing.dumps(str(desafio.pk), salt=_SAL_DESAFIO)


def consultar_desafio(token: str):
    """Lee el desafío sin consumirlo ni contar intentos.

    Solo para la bitácora, que necesita a quién ligar un segundo paso fallido.
    **No sirve para decidir nada de autenticación**: no mira vencimiento ni
    consumo.
    """
    try:
        pk = signing.loads(
            token, salt=_SAL_DESAFIO, max_age=int(DURACION_DESAFIO.total_seconds())
        )
        return MfaDesafio.objects.filter(pk=pk).first()
    except (signing.BadSignature, ValidationError, ValueError):
        return None


def _cargar_desafio(token: str) -> MfaDesafio:
    caducidad = int(DURACION_DESAFIO.total_seconds())
    try:
        pk = signing.loads(token, salt=_SAL_DESAFIO, max_age=caducidad)
    except signing.BadSignature:
        raise ErrorMfa("La verificación expiró. Inicia sesión de nuevo.")
    try:
        return MfaDesafio.objects.select_for_update().get(pk=pk)
    except (MfaDesafio.DoesNotExist, ValidationError, ValueError):
        raise ErrorMfa("La verificación expiró. Inicia sesión de nuevo.")


class Verificacion(NamedTuple):
    """Quién quedó autenticado y con qué."""

    usuario: object
    uso_respaldo: bool


def verificar_desafio(token: str, codigo: str, permitir_respaldo: bool = True):
    """Resuelve el segundo paso y devuelve quién quedó autenticado.

    La fila se bloquea con ``select_for_update`` durante toda la verificación:
    sin eso, varias peticiones a la vez compartirían el contador y el tope de
    ``MAX_INTENTOS`` se sortearía lanzándolas en paralelo.

    Ojo con la estructura: el intento fallido se registra **dentro** de la
    transacción y el error se lanza **fuera**. Al revés, el rollback se llevaría
    el incremento y el tope no contaría nada, que es justo lo que hay que evitar
    en el único sitio donde se prueba un código de seis dígitos.
    """
    with transaction.atomic():
        desafio = _cargar_desafio(token)

        # Estos caminos no escriben nada, así que pueden lanzar desde dentro.
        if desafio.consumido or desafio.expira <= timezone.now():
            raise ErrorMfa("La verificación expiró. Inicia sesión de nuevo.")
        if desafio.intentos >= MAX_INTENTOS:
            raise ErrorMfa("Demasiados intentos fallidos. Inicia sesión de nuevo.")

        codigo = (codigo or "").strip()

        if desafio.metodo in METODOS_ENVIADOS:
            valido = bool(desafio.hash_codigo) and hmac.compare_digest(
                desafio.hash_codigo, _hash(codigo)
            )
        else:
            # Se bloquea también la configuración: `ultimo_contador` es un
            # recurso compartido entre peticiones simultáneas del mismo usuario.
            mfa = MfaUsuario.objects.select_for_update().filter(
                usuario_id=desafio.usuario_id
            ).first()
            valido = bool(mfa) and _verificar_totp(mfa, codigo)

        # El respaldo sirve con cualquier método: es la salida para cuando el
        # habitual no está. No al enrolar, donde se está probando que el factor
        # nuevo funciona y un código viejo no lo prueba.
        uso_respaldo = False
        if not valido and permitir_respaldo:
            uso_respaldo = _consumir_respaldo(desafio.usuario, codigo)
            valido = uso_respaldo

        if valido:
            desafio.consumido = True
            desafio.save(update_fields=["consumido", "actualizado_en"])
            return Verificacion(desafio.usuario, uso_respaldo)

        desafio.intentos += 1
        desafio.save(update_fields=["intentos", "actualizado_en"])
        restantes = MAX_INTENTOS - desafio.intentos

    if restantes <= 0:
        raise ErrorMfa("Demasiados intentos fallidos. Inicia sesión de nuevo.")
    raise ErrorMfa(f"Código incorrecto. Te quedan {restantes} intentos.")


def reenviar_codigo(token: str):
    """Manda un código nuevo para un desafío de correo en curso.

    No toca ``intentos`` ni ``expira``: si el reenvío reiniciara el contador,
    bastaría pedir un correo nuevo cada cinco intentos para tener intentos
    infinitos.
    """
    with transaction.atomic():
        desafio = _cargar_desafio(token)
        if desafio.consumido or desafio.expira <= timezone.now():
            raise ErrorMfa("La verificación expiró. Inicia sesión de nuevo.")
        if desafio.metodo not in METODOS_ENVIADOS:
            raise ErrorMfa("Este método no usa códigos enviados.")
        if desafio.intentos >= MAX_INTENTOS:
            raise ErrorMfa("Demasiados intentos fallidos. Inicia sesión de nuevo.")

        codigo = generar_codigo()
        desafio.hash_codigo = _hash(codigo)
        desafio.save(update_fields=["hash_codigo", "actualizado_en"])

    enviar_codigo(desafio.usuario, codigo)


def enviar_codigo(usuario, codigo: str):
    """Manda el código por correo.

    No propaga los fallos de la pasarela: el desafío ya existe y quien entra
    puede pedir un reenvío o usar un código de respaldo, así que tumbar la
    petición no ayudaría en nada.
    """
    minutos = int(DURACION_DESAFIO.total_seconds() // 60)
    contenido = (
        "<h1>Tu código de verificación</h1>"
        f"<p>Ingrésalo para continuar. Vence en {minutos} minutos.</p>"
        f'<p style="font-size:28px;letter-spacing:6px;"><b>{codigo}</b></p>'
        "<p>Si no intentaste iniciar sesión, cambia tu contraseña.</p>"
    )
    try:
        Zinc().correo_html({
            "correo": usuario.email,
            "asunto": "Código de verificación",
            "contenido": contenido,
            "nombreRemitente": getattr(settings, "ZINC_NOMBRE_REMITENTE", EMISOR),
            "aplicacion": EMISOR.lower(),
        })
    except ZincError as exc:
        logger.warning("No se pudo enviar el código MFA a %s: %s", usuario.email, exc)


# --------------------------------------------------------------------------- #
# Dispositivos recordados
# --------------------------------------------------------------------------- #

def recordar_dispositivo(usuario, agente: str = None, ip: str = None) -> str:
    """Registra el navegador y devuelve el token firmado para la cookie.

    En la base solo queda el HMAC: un volcado no permite fabricar cookies.
    """
    token = secrets.token_urlsafe(32)
    MfaDispositivo.objects.filter(expira__lt=timezone.now()).delete()
    MfaDispositivo.objects.create(
        usuario=usuario,
        hash_token=_hash(token),
        agente=(agente or "")[:500],
        ip=ip,
        ultimo_uso=timezone.now(),
        expira=timezone.now() + DURACION_DISPOSITIVO,
    )
    return signing.dumps(token, salt=_SAL_DISPOSITIVO)


def dispositivo_recordado(usuario, token_firmado: str) -> bool:
    """¿Este navegador puede saltarse el segundo paso?

    Se valida contra el usuario que ya probó su clave, así que la cookie de otra
    cuenta no sirve.
    """
    if not token_firmado:
        return False
    caducidad = int(DURACION_DISPOSITIVO.total_seconds())
    try:
        token = signing.loads(token_firmado, salt=_SAL_DISPOSITIVO, max_age=caducidad)
    except signing.BadSignature:
        return False

    return MfaDispositivo.objects.filter(
        usuario=usuario,
        hash_token=_hash(token),
        expira__gt=timezone.now(),
    ).update(ultimo_uso=timezone.now()) > 0


def olvidar_dispositivos(usuario):
    """Cualquier cambio en cómo se protege la cuenta anula las excepciones."""
    MfaDispositivo.objects.filter(usuario=usuario).delete()


def invalidar_sesiones(usuario):
    """Anula los refresh vivos del usuario.

    Si alguien ya estaba dentro con la clave robada, encender el segundo factor
    tiene que echarlo; si no, solo protegería los ingresos futuros. El access en
    curso sigue sirviendo hasta que venza —15 minutos—, que es el precio de no
    consultar la lista negra en cada petición.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    for token in OutstandingToken.objects.filter(user=usuario):
        BlacklistedToken.objects.get_or_create(token=token)


def mfa_activo(usuario):
    """La configuración si el segundo factor está encendido; si no, ``None``."""
    return MfaUsuario.objects.filter(usuario=usuario, activo=True).first()
