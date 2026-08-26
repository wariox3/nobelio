"""Cliente de Zinc, la pasarela de correo de Semántica.

Envía correos a través de un servicio HTTP propio en vez de hablar SMTP: el
proyecto no necesita saber de servidores de correo, credenciales ni colas, solo
publicar el mensaje en la pasarela.

Se expone solo :meth:`Zinc.correo_html` (``/api/correo/html``), que es el envío
que usa el proyecto: los documentos se entregan con un cuerpo HTML y el zip
adjunto. Zinc tiene más endpoints; se añaden aquí cuando hagan falta.

El contenido de ``datos`` lo define Zinc, no este cliente: se manda tal cual
como JSON. Eso mantiene al cliente al margen de los cambios de la pasarela, a
costa de no validar nada aquí.

La URL base sale de ``settings.ZINC_URL_BASE`` para poder apuntar a otro
ambiente sin tocar código.
"""
from __future__ import annotations

import requests
from django.conf import settings

ZINC_URL_BASE = "http://zinc.semantica.com.co"
ZINC_TIMEOUT = 30  # segundos: un correo con adjuntos tarda más que una consulta

RUTA_CORREO_HTML = "/api/correo/html"


class ZincError(Exception):
    """Error genérico al usar la pasarela de correo."""


class ZincNoDisponible(ZincError):
    """Zinc no respondió correctamente (red, timeout, 4xx/5xx, cuerpo ilegible).

    Es un fallo de la pasarela, no del mensaje: quien llama decide si reintenta.
    """


class Zinc:
    """Cliente HTTP de la pasarela de correo.

    Se instancia sin argumentos para el uso normal; ``url_base`` y ``timeout``
    existen para apuntar a otro ambiente o para las pruebas.
    """

    def __init__(self, url_base: str | None = None, *, timeout: int = ZINC_TIMEOUT):
        self.url_base = (
            url_base or getattr(settings, "ZINC_URL_BASE", ZINC_URL_BASE)
        ).rstrip("/")
        self.timeout = timeout

    # -- Envíos -------------------------------------------------------------

    def correo_html(self, datos: dict) -> dict:
        """Envía un correo con cuerpo HTML. Devuelve la respuesta de Zinc."""
        return self._post(RUTA_CORREO_HTML, datos)

    # -- Interno ------------------------------------------------------------

    def _post(self, ruta: str, datos: dict) -> dict:
        """POST con cuerpo JSON. Traduce cualquier fallo a ``ZincNoDisponible``.

        A diferencia del cliente PHP original, un error de red o un 500 no se
        confunden con "envío sin respuesta": ahí no se sabe si el correo salió,
        y devolver ``None`` en silencio haría que el llamante lo diera por
        enviado.
        """
        try:
            respuesta = requests.post(
                f"{self.url_base}{ruta}",
                json=datos,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            respuesta.raise_for_status()
            return respuesta.json()
        except (requests.RequestException, ValueError) as exc:
            raise ZincNoDisponible(
                f"No se pudo enviar el correo por Zinc ({ruta}): {exc}"
            ) from exc
