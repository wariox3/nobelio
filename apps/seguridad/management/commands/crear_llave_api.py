"""Crea una llave de API para un usuario desde la línea de comandos.

Útil para dar de alta la integración del ERP antes de que exista frontend.
El secreto se muestra una sola vez; cópialo a la configuración del ERP.

Ejemplo::

    # La llave alcanza exactamente lo mismo que el usuario.
    python manage.py crear_llave_api --usuario ana@empresa.co --nombre "ERP producción"

    # Alcance global (todos los emisores), para la aplicación administrativa.
    # El usuario tiene que ser staff; vence en 90 días o en los que se indiquen.
    python manage.py crear_llave_api --usuario admin-app@rededoc.co \
        --nombre "App administrativa" --alcance-global --dias 30
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from django.utils import timezone

from apps.seguridad.models import LlaveApi
from apps.seguridad.models.llave_api import DIAS_MAXIMOS_ALCANCE_GLOBAL

Usuario = get_user_model()


class Command(BaseCommand):
    help = "Crea una llave de API ligada a un usuario y muestra el secreto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario", required=True,
            help="Correo del usuario en cuyo nombre actúa la llave.",
        )
        parser.add_argument(
            "--nombre", required=True,
            help="Nombre descriptivo de la integración (p. ej. 'ERP producción').",
        )
        parser.add_argument(
            "--alcance-global", action="store_true",
            help="Alcanza todos los emisores. Exige un usuario staff.",
        )
        parser.add_argument(
            "--dias", type=int, default=DIAS_MAXIMOS_ALCANCE_GLOBAL,
            help="Días hasta que vence una llave de alcance global "
            f"(máximo {DIAS_MAXIMOS_ALCANCE_GLOBAL}).",
        )

    def handle(self, *args, **opciones):
        try:
            usuario = Usuario.objects.get(email__iexact=opciones["usuario"])
        except Usuario.DoesNotExist:
            raise CommandError(
                f"No existe un usuario con correo {opciones['usuario']!r}."
            )

        alcance_global = opciones["alcance_global"]
        expira_en = None
        if alcance_global:
            if opciones["dias"] < 1:
                raise CommandError("--dias tiene que ser al menos 1.")
            expira_en = timezone.now() + timedelta(days=opciones["dias"])
        try:
            llave, clave_completa = LlaveApi.generar(
                usuario=usuario, nombre=opciones["nombre"],
                expira_en=expira_en, alcance_global=alcance_global,
            )
        except ValueError as error:
            raise CommandError(str(error))

        self.stdout.write(self.style.SUCCESS("Llave de API creada."))
        self.stdout.write(f"  Usuario: {usuario}")
        if alcance_global:
            self.stdout.write("  Alcance: GLOBAL, todos los emisores")
            self.stdout.write(f"  Vence  : {llave.expira_en:%Y-%m-%d %H:%M}")
        else:
            self.stdout.write("  Alcance: los mismos emisores que ese usuario")
        self.stdout.write(f"  Nombre : {llave.nombre}")
        self.stdout.write(f"  Prefijo: {llave.prefijo}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Clave (se muestra una sola vez):"))
        self.stdout.write(f"  {clave_completa}")
        self.stdout.write("")
        self.stdout.write("Cabecera para el ERP:")
        self.stdout.write(f"  Authorization: Api-Key {clave_completa}")
