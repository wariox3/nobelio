"""Crea una llave de API para una cuenta desde la línea de comandos.

Útil para dar de alta la integración del ERP antes de que exista frontend.
El secreto se muestra una sola vez; cópialo a la configuración del ERP.

Ejemplos::

    # Llave normal: alcanza todos los emisores de la cuenta.
    python manage.py crear_llave_api --cuenta 1 --nombre "RedDoc producción"

    # Llave estrecha: solo puede operar un emisor concreto de esa cuenta.
    python manage.py crear_llave_api --cuenta 1 --emisor 7 --nombre "Cliente X"
"""
from django.core.management.base import BaseCommand, CommandError

from apps.cuentas.models import Cuenta
from apps.emisores.models import Emisor
from apps.seguridad.models import LlaveApi


class Command(BaseCommand):
    help = "Crea una llave de API ligada a una cuenta y muestra el secreto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cuenta", required=True, type=int,
            help="ID de la cuenta (la integración) a la que se liga la llave.",
        )
        parser.add_argument(
            "--nombre", required=True,
            help="Nombre descriptivo de la integración (p. ej. 'RedDoc producción').",
        )
        parser.add_argument(
            "--emisor", type=int, default=None,
            help="Opcional: restringe la llave a un único emisor de la cuenta.",
        )

    def handle(self, *args, **opciones):
        try:
            cuenta = Cuenta.objects.get(pk=opciones["cuenta"])
        except Cuenta.DoesNotExist:
            raise CommandError(f"No existe una cuenta con id={opciones['cuenta']}.")

        emisor = None
        if opciones["emisor"] is not None:
            try:
                emisor = Emisor.objects.get(pk=opciones["emisor"])
            except Emisor.DoesNotExist:
                raise CommandError(f"No existe un emisor con id={opciones['emisor']}.")
            if emisor.cuenta_id != cuenta.pk:
                raise CommandError(
                    f"El emisor {emisor} pertenece a otra cuenta ({emisor.cuenta})."
                )

        llave, clave_completa = LlaveApi.generar(
            cuenta=cuenta, emisor=emisor, nombre=opciones["nombre"]
        )

        self.stdout.write(self.style.SUCCESS("Llave de API creada."))
        self.stdout.write(f"  Cuenta : {cuenta}")
        self.stdout.write(
            f"  Alcance: {emisor if emisor else 'todos los emisores de la cuenta'}"
        )
        self.stdout.write(f"  Nombre : {llave.nombre}")
        self.stdout.write(f"  Prefijo: {llave.prefijo}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Clave (se muestra una sola vez):"))
        self.stdout.write(f"  {clave_completa}")
        self.stdout.write("")
        self.stdout.write("Cabecera para el ERP:")
        self.stdout.write(f"  Authorization: Api-Key {clave_completa}")
