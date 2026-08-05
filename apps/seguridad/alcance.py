"""Alcance multi-inquilino: sobre qué emisores puede operar quien hace la petición.

Los datos de la plataforma cuelgan siempre de un emisor, así que aquí se
concentra la única pregunta que hay que responder en cada petición: *¿qué
emisores alcanza este solicitante?* Las tres respuestas posibles son:

- **Staff de la plataforma**: todos (``None`` = sin restricción).
- **Integración (API Key)**: los emisores de la cuenta de la llave, o solo uno
  si la llave está estrechada a un emisor concreto.
- **Usuario humano (JWT)**: los emisores que tenga asignados explícitamente.

La cuenta solo aparece en el caso de la llave. Un usuario no pertenece a
ninguna: su alcance es la lista de emisores que se le asignó, sin más, y sin
emisores no ve nada (falla cerrado).
"""
from rest_framework.exceptions import PermissionDenied

from apps.emisores.models import Emisor

MENSAJE_FUERA_DE_ALCANCE = "No tiene acceso a este emisor."


def es_staff(request):
    """¿La petición viene del staff interno de la plataforma?"""
    usuario = request.user
    return bool(
        getattr(usuario, "is_staff", False) or getattr(usuario, "is_superuser", False)
    )


def _llave(request):
    """La ``LlaveApi`` si la petición viene de una integración; si no, ``None``."""
    return getattr(request.user, "llave", None)


def emisores_permitidos(request):
    """Emisores sobre los que puede operar el solicitante.

    Devuelve un ``QuerySet`` de :class:`~apps.emisores.models.Emisor`, o ``None``
    cuando no hay restricción alguna (staff de la plataforma).
    """
    if es_staff(request):
        return None
    llave = _llave(request)
    if llave is not None:
        if llave.emisor_id:
            return Emisor.objects.filter(pk=llave.emisor_id)
        return Emisor.objects.filter(cuenta_id=llave.cuenta_id)
    return request.user.emisores.all()


def cuenta_de_la_credencial(request):
    """Cuenta que impone la credencial, o ``None`` si no es una integración.

    Solo las API Key llevan cuenta. Un usuario humano no pertenece a ninguna,
    así que para él siempre es ``None``.
    """
    llave = _llave(request)
    return llave.cuenta if llave is not None else None


def puede_operar(request, emisor):
    """¿El solicitante alcanza a ``emisor``?"""
    permitidos = emisores_permitidos(request)
    if permitidos is None:
        return True
    if emisor is None:
        return False
    return permitidos.filter(pk=emisor.pk).exists()


def exigir_alcance(request, emisor):
    """Lanza 403 si el solicitante no alcanza a ``emisor``."""
    if not puede_operar(request, emisor):
        raise PermissionDenied(MENSAJE_FUERA_DE_ALCANCE)


class AlcanceEmisorMixin:
    """Restringe un ``ViewSet`` a los emisores que alcanza el solicitante.

    Filtra el queryset en lectura y comprueba el emisor recibido en escritura,
    de modo que una integración no pueda ni ver ni crear datos de otra cuenta.

    ``campo_emisor`` es la ruta ORM del modelo hasta el emisor (``"emisor"`` en
    casi todos; ``"id"`` cuando el propio modelo es el emisor).
    """

    campo_emisor = "emisor"

    def get_queryset(self):
        qs = super().get_queryset()
        permitidos = emisores_permitidos(self.request)
        if permitidos is None:
            return qs
        return qs.filter(**{f"{self.campo_emisor}__in": permitidos})

    def exigir_alcance_de(self, serializer):
        """Valida el emisor que trae el serializer (o el de la instancia)."""
        emisor = serializer.validated_data.get("emisor")
        if emisor is None and serializer.instance is not None:
            emisor = getattr(serializer.instance, "emisor", None)
        exigir_alcance(self.request, emisor)

    def perform_create(self, serializer):
        self.exigir_alcance_de(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self.exigir_alcance_de(serializer)
        serializer.save()
