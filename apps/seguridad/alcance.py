"""Alcance multi-inquilino: sobre qué emisores puede operar quien hace la petición.

Los datos de la plataforma cuelgan siempre de un emisor, así que aquí se
concentra la única pregunta que hay que responder en cada petición: *¿qué
emisores alcanza este solicitante?* Las tres respuestas posibles son:

- **Staff de la plataforma**: todos (``None`` = sin restricción).
- **Integración (API Key)**: los emisores de la cuenta de la llave.
- **Usuario humano (JWT)**: los de las cuentas que posea, más los que le hayan
  asignado uno a uno.

Un usuario sigue sin *pertenecer* a una cuenta, pero puede **poseerla**: quien
se registra queda como propietario de la que se le crea, y desde ahí alcanza
todos sus emisores sin que nadie se los asigne. Las dos vías se suman, así que
un contador puede ser dueño de su cuenta y tener además un emisor suelto de un
cliente. Sin ninguna de las dos no ve nada (falla cerrado).

Al **crear** un emisor la pregunta es la otra mitad: *¿de qué cuenta puede
colgarlo?* La responde ``cuenta_permitida``, que es la única definición de esa
regla en el proyecto (``exigir_cuenta`` es su versión que lanza 403).
"""
from django.core.exceptions import ValidationError as ErrorValidacionDjango
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor

MENSAJE_FUERA_DE_ALCANCE = "No tiene acceso a este emisor."
MENSAJE_FUERA_DE_CUENTA = (
    "La credencial solo puede operar sobre su propia cuenta."
)
MENSAJE_SIN_CUENTA = (
    "Para dar de alta un emisor hay que ser dueño de una cuenta."
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
    usuario = request.user
    if not getattr(usuario, "is_authenticated", False):
        return Emisor.objects.none()
    # Una persona alcanza por dos vías que se suman: los emisores de las cuentas
    # que posee —todos, sin asignar nada, que es lo que gana quien se registra— y
    # los que le hayan asignado uno a uno, que pueden ser de cuentas ajenas. El
    # `distinct` es por el join del M2M, que si no repite filas.
    return Emisor.objects.filter(
        Q(cuenta__usuario=usuario) | Q(usuarios=usuario)
    ).distinct()


def cuentas_propias(request):
    """Cuentas de las que el solicitante es dueño; vacío si no lo es de ninguna.

    Solo las personas son dueñas de una cuenta. Una integración se identifica
    por su llave, y su cuenta sale de ahí, no de la propiedad.
    """
    if _llave(request) is not None:
        return Cuenta.objects.none()
    usuario = request.user
    if not getattr(usuario, "is_authenticated", False):
        return Cuenta.objects.none()
    return Cuenta.objects.filter(usuario=usuario)


def cuenta_propia(request):
    """Cuenta de la que cuelga lo que dé de alta el solicitante, o ``None``.

    Para una integración es la de su llave. Para una persona, la cuenta que
    posee —la que se le crea al registrarse—, que es lo que le permite dar de
    alta emisores sin ser staff.

    Devuelve ``None`` cuando no hay una respuesta única: ni llave ni cuenta
    propia, o varias cuentas propias. En ese último caso la cuenta se indica en
    el cuerpo y ``cuenta_permitida`` comprueba que sea suya.
    """
    llave = _llave(request)
    if llave is not None:
        return llave.cuenta
    propias = list(cuentas_propias(request)[:2])
    return propias[0] if len(propias) == 1 else None


def puede_dar_de_alta(request):
    """¿El solicitante tiene una cuenta de la que colgar un emisor nuevo?

    La tienen la integración (lo cuelga de la de su llave), el staff (indica
    cuál) y el dueño de una cuenta (la suya). Quien solo tiene emisores
    asignados no da de alta: opera lo que le dieron, no abre nuevos.
    """
    return (
        cuenta_propia(request) is not None
        or es_staff(request)
        or cuentas_propias(request).exists()
    )


def cuenta_permitida(request, cuenta):
    """¿El solicitante puede colgar datos de ``cuenta``?

    Regla única del alta multi-inquilino: el staff elige la cuenta libremente;
    una integración solo puede usar la de su llave; una persona, solo una que
    posea; quien no tiene ninguna no crea nada.
    """
    llave = _llave(request)
    if llave is not None:
        return cuenta is not None and cuenta.pk == llave.cuenta_id
    if es_staff(request):
        return True
    return cuenta is not None and cuentas_propias(request).filter(pk=cuenta.pk).exists()


def exigir_cuenta(request, cuenta):
    """Lanza 403 si el solicitante no puede colgar datos de ``cuenta``."""
    if not cuenta_permitida(request, cuenta):
        raise PermissionDenied(
            MENSAJE_FUERA_DE_CUENTA if puede_dar_de_alta(request)
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
