"""Alcance multi-inquilino: sobre qué emisores puede operar quien hace la petición.

Los datos de la plataforma cuelgan siempre de un emisor, así que aquí se
concentra la única pregunta que hay que responder en cada petición: *¿qué
emisores alcanza este solicitante?* Las tres respuestas posibles son:

- **Staff de la plataforma**: todos (``None`` = sin restricción).
- **Integración (API Key)**: los emisores de la cuenta de la llave.
- **Usuario humano (JWT)**: los emisores que tenga asignados explícitamente.

La cuenta solo aparece en el caso de la llave. Un usuario no pertenece a
ninguna: su alcance es la lista de emisores que se le asignó, sin más, y sin
emisores no ve nada (falla cerrado).

Al **crear** un emisor la pregunta es la otra mitad: *¿de qué cuenta puede
colgarlo?* La responde ``cuenta_permitida``, que es la única definición de esa
regla en el proyecto (``exigir_cuenta`` es su versión que lanza 403).
"""
from django.core.exceptions import ValidationError as ErrorValidacionDjango
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.emisores.models import Emisor

MENSAJE_FUERA_DE_ALCANCE = "No tiene acceso a este emisor."
MENSAJE_FUERA_DE_CUENTA = (
    "La credencial solo puede operar sobre su propia cuenta."
)
MENSAJE_SIN_CUENTA = (
    "Solo el staff o una integración pueden dar de alta emisores."
)


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
        return Emisor.objects.filter(cuenta_id=llave.cuenta_id)
    return request.user.emisores.all()


def cuenta_de_la_credencial(request):
    """Cuenta que impone la credencial, o ``None`` si no es una integración.

    Solo las API Key llevan cuenta. Un usuario humano no pertenece a ninguna,
    así que para él siempre es ``None``.
    """
    llave = _llave(request)
    return llave.cuenta if llave is not None else None


def puede_dar_de_alta(request):
    """¿El solicitante tiene una cuenta de la que colgar un emisor nuevo?

    Solo la integración (que lo cuelga de la suya) y el staff (que indica cuál).
    Un usuario humano no pertenece a ninguna cuenta, así que no crea emisores:
    el alta es del staff o del ERP, y después se le asignan los emisores.
    """
    return cuenta_de_la_credencial(request) is not None or es_staff(request)


def cuenta_permitida(request, cuenta):
    """¿El solicitante puede colgar datos de ``cuenta``?

    Regla única del alta multi-inquilino: el staff elige la cuenta libremente;
    una integración solo puede usar la suya; quien no tiene cuenta no crea nada.
    """
    propia = cuenta_de_la_credencial(request)
    if propia is None:
        return es_staff(request)
    return cuenta is not None and cuenta.pk == propia.pk


def exigir_cuenta(request, cuenta):
    """Lanza 403 si el solicitante no puede colgar datos de ``cuenta``."""
    if not cuenta_permitida(request, cuenta):
        raise PermissionDenied(
            MENSAJE_FUERA_DE_CUENTA if cuenta_de_la_credencial(request)
            else MENSAJE_SIN_CUENTA
        )


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


class RelacionDelAlcance(serializers.PrimaryKeyRelatedField):
    """Campo de relación acotado a los emisores que alcanza el solicitante.

    Sin esto, un id ajeno y un id inexistente se distinguen por el mensaje de
    error ("no pertenece al emisor" frente a "no existe"), y eso convierte al
    endpoint en un oráculo: un cliente autenticado puede averiguar qué ids hay
    en otras cuentas. Filtrando el queryset las dos respuestas son idénticas.

    ``campo_emisor`` es la ruta ORM del modelo hasta el emisor (``"emisor"`` en
    casi todos; ``"id"`` cuando el propio modelo es el emisor).
    """

    def __init__(self, *args, campo_emisor="emisor", **kwargs):
        self.campo_emisor = campo_emisor
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        """Un id con formato inválido es un 400, no un 500.

        DRF solo traduce ``TypeError``/``ValueError`` al buscar por pk, y un
        UUID mal formado hace que Django lance su propio ``ValidationError``,
        que no hereda de ninguno de los dos: sin esto sale un 500 con traceback.
        Se responde como un id inexistente —que es lo que es— y de paso se
        mantiene la indistinguibilidad que persigue este campo.
        """
        try:
            return super().to_internal_value(data)
        except ErrorValidacionDjango:
            self.fail("does_not_exist", pk_value=data)

    def get_queryset(self):
        qs = super().get_queryset()
        request = self.context.get("request")
        if request is None:
            # Fuera de una petición (shell, pruebas del serializer suelto) no
            # hay a quién acotar; la pertenencia la sigue validando el propio
            # serializer.
            return qs
        permitidos = emisores_permitidos(request)
        if permitidos is None:
            return qs
        return qs.filter(**{f"{self.campo_emisor}__in": permitidos})


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
