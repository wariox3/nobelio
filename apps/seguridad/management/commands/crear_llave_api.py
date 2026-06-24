"""Crea una llave de API para un emisor desde la línea de comandos.

Útil para dar de alta la integración del ERP antes de que exista frontend.
El secreto se muestra una sola vez; cópialo a la configuración del ERP.

Ejemplo::

    python manage.py crear_llave_api --emisor 1 --nombre "ERP producción"
"""
from django.core.management.base import BaseCommand, CommandError

from apps.emisores.models import Emisor
from apps.seguridad.models import LlaveApi


class Command(BaseCommand):
    help = "Crea una llave de API ligada a un emisor y muestra el secreto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--emisor", required=True, type=int,
            help="ID del emisor al que se liga la llave.",
        )
        parser.add_argument(
            "--nombre", required=True,
            help="Nombre descriptivo de la integración (p. ej. 'ERP producción').",
        )

    def handle(self, *args, **opciones):
        try:
            emisor = Emisor.objects.get(pk=opciones["emisor"])
        except Emisor.DoesNotExist:
            raise CommandError(f"No existe un emisor con id={opciones['emisor']}.")

        llave, clave_completa = LlaveApi.generar(
            emisor=emisor, nombre=opciones["nombre"]
        )

        self.stdout.write(self.style.SUCCESS("Llave de API creada."))
        self.stdout.write(f"  Emisor : {emisor}")
        self.stdout.write(f"  Nombre : {llave.nombre}")
        self.stdout.write(f"  Prefijo: {llave.prefijo}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Clave (se muestra una sola vez):"))
        self.stdout.write(f"  {clave_completa}")
        self.stdout.write("")
        self.stdout.write("Cabecera para el ERP:")
        self.stdout.write(f"  Authorization: Api-Key {clave_completa}")
