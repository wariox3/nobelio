"""Modelo de usuario del sistema (autenticación por email)."""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.nucleo.models import ModeloConFechas
from apps.seguridad.managers import UsuarioManager


class Usuario(AbstractBaseUser, PermissionsMixin, ModeloConFechas):
    """Usuario del sistema. El email es el identificador de acceso."""

    email = models.EmailField(_("correo electrónico"), unique=True)
    nombre_corto = models.CharField(max_length=255, null=True)

    is_staff = models.BooleanField(
        _("acceso al admin"),
        default=False,
        help_text=_("Indica si el usuario puede entrar al sitio de administración."),
    )
    is_active = models.BooleanField(
        _("activo"),
        default=True,
        help_text=_("Indica si la cuenta está activa. Desmarcar en lugar de borrar."),
    )
    # Aparte de `is_active` a propósito: "nunca verificó su correo" y "lo
    # suspendí" son dos estados distintos, y el día que haya que suspender a
    # alguien por abuso hay que poder distinguirlos. El precio es que la regla
    # no sale gratis —SimpleJWT solo mira `is_active`—, así que la impone el
    # serializer de login (`apps.seguridad.serializers.token`).
    is_verified = models.BooleanField(
        _("correo verificado"),
        default=False,
        help_text=_(
            "El usuario confirmó su correo. Sin esto no puede iniciar sesión."
        ),
    )

    # --- Relaciones ---
    # El usuario sigue sin pertenecer a una cuenta: los emisores que alcanza son
    # los que tenga asignados aquí, uno a uno. Lo que cambia con el registro
    # abierto es que ahora puede además *ser dueño* de una cuenta
    # (`Cuenta.propietario`), y por esa vía alcanza todos los emisores de esa
    # cuenta sin tener que asignárselos. Las dos vías se suman en
    # `apps.seguridad.alcance.emisores_permitidos`.
    #
    # Sin emisores asignados y sin cuenta propia, un usuario no staff no ve nada
    # (falla cerrado). La granularidad de este M2M es lo que permite que un
    # contador vea un solo emisor de los varios de un cliente.
    emisores = models.ManyToManyField(
        "emisores.Emisor",
        related_name="usuarios",
        verbose_name="emisores",
        blank=True,
        help_text="Emisores cuyos datos puede consultar y operar el usuario.",
    )

    objects = UsuarioManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "seg_usuario"
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ("email",)

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.nombre_corto or self.email

    def get_short_name(self):
        return self.nombre_corto or self.email
