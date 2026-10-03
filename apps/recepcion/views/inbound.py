"""Entrada de los correos que publica el Email Worker de Cloudflare."""
import hashlib
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.nucleo.registro import campos
from apps.recepcion.models import Correo

logger = logging.getLogger(__name__)

# Tope del MIME crudo. Cloudflare Email Routing no entrega correos más grandes.
MAXIMO_BYTES = 30 * 1024 * 1024
TROZO_BYTES = 64 * 1024


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
    """Registra el correo que llega a ``<alias>@recepcion.rededoc.co``.

    Solo lo registra: no abre el MIME ni sus adjuntos. Es idempotente por el
    SHA-256 del body, porque el Worker reintenta el POST si no recibe
    respuesta: el mismo correo responde 200 y no crea un segundo registro.

    **Sin autenticación por ahora**, por decisión expresa mientras se arma el
    flujo paso a paso. Falta validar el Bearer contra ``INBOUND_TOKEN``.
    """
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

    correo, creado = Correo.objects.get_or_create(
        sha256=sha256,
        defaults={
            "alias": envelope_to.rsplit("@", 1)[0].lower(),
            "envelope_to": envelope_to,
            "envelope_from": request.headers.get("X-Envelope-From", "").strip(),
            "raw_key": request.headers.get("X-Raw-Key", "").strip(),
        },
    )
    logger.info("recepcion.correo %s", campos(
        correo=correo.pk, alias=correo.alias, nuevo=creado,
    ))
    return JsonResponse({"id": correo.pk, "estado": correo.estado}, status=201 if creado else 200)
