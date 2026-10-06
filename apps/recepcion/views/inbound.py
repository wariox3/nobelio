"""Entrada de los correos que publica el Email Worker de Cloudflare."""
import hashlib
import hmac
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.nucleo.colas import encolar
from apps.nucleo.registro import campos
from apps.recepcion.models import Correo
from apps.recepcion.procesamiento import emisor_del_alias
from apps.recepcion.tareas import procesar_correo

logger = logging.getLogger(__name__)

# Tope del MIME crudo. Cloudflare Email Routing no entrega correos más grandes.
MAXIMO_BYTES = 30 * 1024 * 1024
TROZO_BYTES = 64 * 1024


def _token_valido(request):
    """¿El ``Authorization: Bearer`` coincide con ``INBOUND_TOKEN``?

    En tiempo constante, para que el tiempo de respuesta no vaya revelando el
    token carácter a carácter. Sin token configurado no pasa nada: falla
    cerrado.
    """
    esperado = settings.INBOUND_TOKEN
    if not esperado:
        logger.error("recepcion.sin_token INBOUND_TOKEN no está configurado")
        return False
    palabra, _, recibido = request.headers.get("Authorization", "").partition(" ")
    if palabra.lower() != "bearer":
        return False
    return hmac.compare_digest(recibido.strip().encode(), esperado.encode())


class CuerpoDemasiadoGrande(Exception):
    """El body supera ``MAXIMO_BYTES``."""


def _sha256_del_cuerpo(request):
    """El SHA-256 del body, leyéndolo por trozos y sin guardarlo.

    Por stream y no con ``request.body``: ese aplica el tope de Django
    (``DATA_UPLOAD_MAX_MEMORY_SIZE``, 2.5 MB) y además deja el correo entero
    en memoria. Aquí solo hace falta la huella; el MIME vive en R2.
    """
    huella = hashlib.sha256()
    leidos = 0
    while trozo := request.read(TROZO_BYTES):
        leidos += len(trozo)
        if leidos > MAXIMO_BYTES:
            raise CuerpoDemasiadoGrande
        huella.update(trozo)
    return huella.hexdigest()


@csrf_exempt
@require_POST
def inbound(request):
    """Registra el correo que llega a ``<nit>@recepcion.rededoc.co``.

    Lo asocia al emisor de ese NIT y encola ``procesar_correo``, pero no abre
    el MIME: eso lo hace la tarea, descargándolo de R2. Es idempotente por el
    SHA-256 del body, porque el Worker reintenta el POST si no recibe
    respuesta: el mismo correo responde 200 y no crea un segundo registro.

    Lo publica el Email Worker de Cloudflare con ``Authorization: Bearer
    <INBOUND_TOKEN>``; sin él, 401 antes de leer nada del body.
    """
    if not _token_valido(request):
        return JsonResponse({"detail": "Token inválido."}, status=401)

    envelope_to = request.headers.get("X-Envelope-To", "").strip()
    if "@" not in envelope_to:
        return JsonResponse({"detail": "Falta la cabecera X-Envelope-To."}, status=400)

    largo = request.META.get("CONTENT_LENGTH") or ""
    if largo.isdigit() and int(largo) > MAXIMO_BYTES:
        return JsonResponse({"detail": "El correo supera los 30 MB."}, status=413)
    try:
        sha256 = _sha256_del_cuerpo(request)
    except CuerpoDemasiadoGrande:
        return JsonResponse({"detail": "El correo supera los 30 MB."}, status=413)

    alias = envelope_to.rsplit("@", 1)[0].lower()
    correo, creado = Correo.objects.get_or_create(
        sha256=sha256,
        defaults={
            "alias": alias,
            "emisor": emisor_del_alias(alias),
            "envelope_to": envelope_to,
            "envelope_from": request.headers.get("X-Envelope-From", "").strip(),
            "raw_key": request.headers.get("X-Raw-Key", "").strip(),
        },
    )
    # También el repetido si sigue pendiente: si la primera vez el broker no
    # respondió, el reintento del Worker es lo que lo vuelve a encolar. La
    # tarea no procesa dos veces lo mismo.
    if creado or correo.estado == Correo.Estado.PENDIENTE:
        encolar(procesar_correo, correo.pk)
    logger.info("recepcion.correo %s", campos(
        correo=correo.pk, alias=correo.alias, emisor=correo.emisor_id, nuevo=creado,
    ))
    return JsonResponse({"id": correo.pk, "estado": correo.estado}, status=201 if creado else 200)
