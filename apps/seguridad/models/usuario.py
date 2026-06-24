"""Modelo de usuario del sistema (autenticación por email)."""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.nucleo.models import ModeloConFechas
from apps.seguridad.managers import UsuarioManager


class Usuario(AbstractBaseUser, PermissionsMixin, ModeloConFechas):
    """Usuario del sistema. El email es el identificador de acceso."""

    email = models.EmailField(_("correo electrónico"), unique=True)
    nombres = models.CharField(_("nombres"), max_length=150, blank=True)
    apellidos = models.CharField(_("apellidos"), max_length=150, blank=True)

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

    # --- Relaciones ---
    # Nullable: el staff interno de la plataforma no pertenece a ninguna cuenta;
    # los usuarios de un cliente sí están ligados a su cuenta (tenant).
    cuenta = models.ForeignKey(
        "cuentas.Cuenta",
        on_delete=models.PROTECT,
        related_name="usuarios",
        verbose_name="cuenta",
        null=True,
        blank=True,
    )

    objects = UsuarioManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ("email",)

    def __str__(self):
        return self.email

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}".strip()

    def get_full_name(self):
        return self.nombre_completo or self.email

    def get_short_name(self):
        return self.nombres or self.email
