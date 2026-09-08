"""Crea una llave de API para un usuario desde la línea de comandos.

Útil para dar de alta la integración del ERP antes de que exista frontend.
El secreto se muestra una sola vez; cópialo a la configuración del ERP.

Ejemplo::

    # La llave alcanza exactamente lo mismo que el usuario.
    python manage.py crear_llave_api --usuario ana@empresa.co --nombre "ERP producción"
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.seguridad.models import LlaveApi

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

    def handle(self, *args, **opciones):
        try:
            usuario = Usuario.objects.get(email__iexact=opciones["usuario"])
        except Usuario.DoesNotExist:
            raise CommandError(
                f"No existe un usuario con correo {opciones['usuario']!r}."
            )

        llave, clave_completa = LlaveApi.generar(
            usuario=usuario, nombre=opciones["nombre"]
        )

        self.stdout.write(self.style.SUCCESS("Llave de API creada."))
        self.stdout.write(f"  Usuario: {usuario}")
        self.stdout.write("  Alcance: los mismos emisores que ese usuario")
        self.stdout.write(f"  Nombre : {llave.nombre}")
        self.stdout.write(f"  Prefijo: {llave.prefijo}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Clave (se muestra una sola vez):"))
        self.stdout.write(f"  {clave_completa}")
        self.stdout.write("")
        self.stdout.write("Cabecera para el ERP:")
        self.stdout.write(f"  Authorization: Api-Key {clave_completa}")
