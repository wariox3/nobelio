"""Manager del modelo de usuario (login por email)."""
from django.contrib.auth.base_user import BaseUserManager


class UsuarioManager(BaseUserManager):
    """Manager donde el email es el identificador único, sin username."""

    use_in_migrations = True

    def _crear_usuario(self, email, password, **extra_fields):
        if not email:
            raise ValueError("El email es obligatorio.")
        email = self.normalize_email(email)
        # Sin nombre corto se usa lo que va antes de la @. Es lo que la persona
        # reconoce como suyo, y evita que la interfaz y los correos tengan que
        # saludar con la dirección entera cuando nadie escribió un nombre.
        # Solo al crear: si después se vacía a propósito, se respeta.
        if not extra_fields.get("nombre_corto"):
            extra_fields["nombre_corto"] = email.partition("@")[0]
        usuario = self.model(email=email, **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._crear_usuario(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self._crear_usuario(email, password, **extra_fields)
