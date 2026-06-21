"""
Comando para inspeccionar las listas de valores DIAN (.gc) desde la terminal.

Ejemplos:
    python manage.py listas                 # resumen de todas las listas
    python manage.py listas TipoDocumento   # filas de una lista concreta
"""
from django.core.management.base import BaseCommand, CommandError

from apps.catalogos import genericode as gc


class Command(BaseCommand):
    help = "Inspecciona las listas de valores DIAN en formato Genericode (.gc)."

    def add_arguments(self, parser):
        parser.add_argument(
            "nombre",
            nargs="?",
            help="Nombre de la lista a mostrar (p. ej. TipoDocumento). "
            "Si se omite, muestra un resumen de todas.",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=0,
            help="Máximo de filas a mostrar de una lista (0 = todas).",
        )

    def handle(self, *args, **opciones):
        nombre = opciones.get("nombre")
        if not nombre:
            self._resumen()
            return

        try:
            lista = gc.cargar(nombre)
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        self._detalle(lista, opciones["limite"])

    def _resumen(self):
        archivos = gc.listar_archivos()
        self.stdout.write(self.style.MIGRATE_HEADING(f"{len(archivos)} listas disponibles:\n"))
        for archivo in archivos:
            lista = gc.parsear_archivo(archivo)
            self.stdout.write(
                f"  {lista.nombre_corto:<26} {len(lista):>5} filas   ({archivo.name})"
            )

    def _detalle(self, lista, limite):
        cols = [c.id for c in lista.columnas] or list(
            (lista.filas[0].keys() if lista.filas else [])
        )
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{lista.nombre_corto} (v{lista.version}) — {len(lista)} filas"
            )
        )
        self.stdout.write(f"Columnas: {', '.join(cols)}\n")
        filas = lista.filas[:limite] if limite else lista.filas
        for fila in filas:
            self.stdout.write("  " + "  ".join(f"{k}={v}" for k, v in fila.items()))
        if limite and len(lista) > limite:
            self.stdout.write(self.style.WARNING(f"  … {len(lista) - limite} filas más"))
