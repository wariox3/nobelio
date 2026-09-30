"""
Carga o actualiza los catálogos desde sus listas `.gc`.

Es idempotente: se puede repetir tras cambiar una lista. El trabajo lo hace
`apps.catalogos.carga`.

    python manage.py cargar_catalogos
"""
from django.core.management.base import BaseCommand, CommandError

from apps.catalogos.carga import cargar


class Command(BaseCommand):
    help = "Carga o actualiza los catálogos desde sus listas .gc."

    def handle(self, *args, **opciones):
        try:
            resultado = cargar()
        except (ValueError, FileNotFoundError) as error:
            raise CommandError(str(error)) from error

        for lista, creados, actualizados in resultado:
            self.stdout.write(
                f"  {lista.etiqueta:<36} "
                f"{creados:>4} creados, {actualizados:>4} actualizados"
            )
        self.stdout.write(self.style.SUCCESS("Catálogos cargados."))
