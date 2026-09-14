"""Aviso interno de cada usuario nuevo.

Cuando se crea un usuario —por el registro público o por la API de usuarios—
sale un correo por Zinc a ``settings.CORREO_AVISO_USUARIO_NUEVO`` con sus datos.
Es un aviso para quien administra la plataforma, no para el usuario: el de él
es el de verificación (ver ``verificacion``).

Lleva lo que sirve para reconocer el alta —correo, nombre, de dónde vino, sus
banderas y sus emisores— y **nunca la contraseña**, ni en claro ni su hash.

Lo que escribió quien se registra se escapa antes de meterlo en el HTML: el
registro es público, y un ``nombre_corto`` con etiquetas acabaría pintado en la
bandeja de quien administra.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from apps.utilidades.zinc import Zinc, ZincError

logger = logging.getLogger(__name__)

APLICACION = "nobelio"

ORIGEN_REGISTRO = "Registro público"
ORIGEN_API = "API de usuarios"


def destinatario() -> str:
    """El correo que recibe el aviso. Vacío significa que está desactivado."""
    return getattr(settings, "CORREO_AVISO_USUARIO_NUEVO", "") or ""


def _si_no(valor) -> str:
    return "Sí" if valor else "No"


def _filas(usuario, origen, creado_por):
    """Los datos del usuario que van en el aviso, como pares (campo, valor)."""
    emisores = ", ".join(e.razon_social for e in usuario.emisores.all())
    creado = (
        timezone.localtime(usuario.creado_en).strftime("%Y-%m-%d %H:%M")
        if usuario.creado_en else ""
    )
    return [
        ("ID", usuario.pk),
        ("Correo", usuario.email),
        ("Nombre corto", usuario.nombre_corto or ""),
        ("Origen", origen),
        ("Creado por", creado_por or "—"),
        ("Staff", _si_no(usuario.is_staff)),
        ("Superusuario", _si_no(usuario.is_superuser)),
        ("Activo", _si_no(usuario.is_active)),
        ("Correo verificado", _si_no(usuario.is_verified)),
        ("Emisores", emisores or "Ninguno"),
        ("Creado en", creado),
    ]


def cuerpo_html(usuario, *, origen, creado_por=None) -> str:
    """Cuerpo del aviso: una tabla con los datos del usuario."""
    filas = "".join(
        f'<tr><th align="left">{escape(campo)}</th><td>{escape(valor)}</td></tr>'
        for campo, valor in _filas(usuario, origen, creado_por)
    )
    return (
        f"<p>Se creó un usuario nuevo en {APLICACION}.</p>"
        f'<table cellpadding="4">{filas}</table>'
    )


def payload_zinc(usuario, *, origen, creado_por=None) -> dict:
    """Arma el cuerpo que espera Zinc en ``/api/correo/html``."""
    return {
        "correo": destinatario(),
        "asunto": f"Nuevo usuario: {usuario.email}",
        "contenido": cuerpo_html(usuario, origen=origen, creado_por=creado_por),
        "nombreRemitente": getattr(settings, "ZINC_NOMBRE_REMITENTE", APLICACION),
        "aplicacion": APLICACION,
    }


def enviar_aviso_usuario_nuevo(usuario, *, origen, creado_por=None, zinc=None) -> bool:
    """Envía el aviso. Devuelve si salió.

    **No propaga el fallo**, por lo mismo que la verificación: el usuario ya
    existe cuando esto se llama, y un mal momento de la pasarela no puede
    tumbar el alta. Queda en el log.

    El cliente se puede inyectar para las pruebas.
    """
    if not destinatario():
        return False
    cliente = zinc or Zinc()
    try:
        respuesta = cliente.correo_html(
            payload_zinc(usuario, origen=origen, creado_por=creado_por)
        )
    except ZincError:
        logger.exception("No se pudo enviar el aviso del usuario nuevo %s", usuario.pk)
        return False
    if respuesta.get("error"):
        logger.error(
            "Zinc rechazó el aviso del usuario nuevo %s: %s", usuario.pk, respuesta
        )
        return False
    return True


def avisar_al_confirmar(usuario, *, origen, creado_por=None):
    """Programa el aviso para cuando la transacción del alta haya confirmado.

    Si el alta hiciera rollback no saldría un aviso de un usuario que no llegó a
    existir. Fuera de una transacción, ``on_commit`` lo ejecuta en el acto.
    """
    transaction.on_commit(
        lambda: enviar_aviso_usuario_nuevo(
            usuario, origen=origen, creado_por=creado_por,
        )
    )
