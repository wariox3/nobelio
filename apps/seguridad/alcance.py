"""Alcance: sobre qué emisores puede operar quien hace la petición.

Los datos de la plataforma cuelgan siempre de un emisor, así que aquí se
concentra la única pregunta que hay que responder en cada petición: *¿qué
emisores alcanza este solicitante?*

- **Staff de la plataforma**: todos (``None`` = sin restricción).
- **Cualquier otro**: los que posee, más los que le hayan asignado uno a uno.

Una integración (API Key) no tiene alcance propio: **actúa en nombre de su
usuario** y alcanza exactamente lo mismo que él. Por eso hay una sola regla y no
dos que puedan divergir; antes había un concepto intermedio (la cuenta) con su
propia regla, y desapareció.

Las dos vías se suman: alguien puede ser dueño de sus emisores y tener además
uno ajeno compartido. Sin ninguna de las dos no ve nada (falla cerrado).
"""
from django.core.exceptions import ValidationError as ErrorValidacionDjango
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.emisores.models import Emisor

MENSAJE_FUERA_DE_ALCANCE = "No tiene acceso a este emisor."


def es_staff(request):
    """¿La petición viene del staff interno de la plataforma?"""
    usuario = request.user
    return bool(
        getattr(usuario, "is_staff", False) or getattr(usuario, "is_superuser", False)
    )


def usuario_del_request(request):
    """La persona en cuyo nombre se actúa, o ``None`` si no hay ninguna.

    Para una petición con cookie es quien inició sesión. Para una con API Key es
    el dueño de la llave: el principal de la llave no es un modelo, así que hay
    que sacar de él al usuario de verdad.
    """
    solicitante = getattr(request, "user", None)
    llave = getattr(solicitante, "llave", None)
    if llave is not None:
        return llave.usuario
    if getattr(solicitante, "is_authenticated", False):
        return solicitante
    return None


def emisores_permitidos(request):
    """Emisores sobre los que puede operar el solicitante.

    Devuelve un ``QuerySet`` de :class:`~apps.emisores.models.Emisor`, o ``None``
    cuando no hay restricción alguna (staff de la plataforma).
    """
    if es_staff(request):
        return None
    usuario = usuario_del_request(request)
    if usuario is None:
        return Emisor.objects.none()
    # Las dos vías se suman: los propios y los compartidos. El `distinct` es por
    # el join del M2M, que si no repite filas.
    return Emisor.objects.filter(
        Q(usuario=usuario) | Q(usuarios=usuario)
    ).distinct()


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
    fuera de su alcance. Filtrando el queryset las dos respuestas son idénticas.

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
    de modo que nadie pueda ni ver ni crear datos de un emisor ajeno.

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
