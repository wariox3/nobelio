"""Vuelve a procesar correos de recepción, descargando su MIME de R2.

Para los que se quedaron en el camino: en ``error`` (R2 caído más allá de los
reintentos, un bug ya corregido), ``pendiente`` (el broker no recibió la
tarea) o ``empresa_desconocida`` (llegaron antes de que registraran a su
emisor). Corre aquí mismo, no en Celery, y muestra cómo quedó cada uno.

Ejemplo::

    # Uno.
    python manage.py reprocesar_correos --id 42

    # Todos los reprocesables. Los pendientes de menos de 30 minutos se saltan:
    # pueden estar todavía en la cola.
    python manage.py reprocesar_correos --todos
"""
from django.core.management.base import BaseCommand, CommandError

from apps.recepcion import procesamiento
from apps.recepcion.models import Correo


class Command(BaseCommand):
    help = "Vuelve a procesar correos de recepción en error, pendientes o de empresa desconocida."

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument("--id", type=int, help="El correo a reprocesar.")
        grupo.add_argument(
            "--todos", action="store_true",
            help="Todos los correos en error, pendientes o de empresa desconocida.",
        )

    def handle(self, *args, **opciones):
        if opciones["id"] is not None:
            if not Correo.objects.filter(pk=opciones["id"]).exists():
                raise CommandError(f"No existe el correo {opciones['id']}.")
            try:
                self._reprocesar(opciones["id"])
            except procesamiento.NoReprocesable as error:
                raise CommandError(str(error))
            return

        ids = list(procesamiento.reprocesables().values_list("pk", flat=True))
        if not ids:
            self.stdout.write("No hay correos para reprocesar.")
            return
        conteo = {}
        for correo_id in ids:
            try:
                estado = self._reprocesar(correo_id)
            except procesamiento.NoReprocesable as error:
                # Cambió entre la consulta y aquí (lo procesó la tarea).
                self.stdout.write(f"  {error}")
                estado = "omitido"
            conteo[estado] = conteo.get(estado, 0) + 1
        resumen = ", ".join(f"{estado}: {n}" for estado, n in sorted(conteo.items()))
        self.stdout.write(self.style.SUCCESS(f"{len(ids)} correos reprocesados ({resumen})."))

    def _reprocesar(self, correo_id):
        """Reprocesa un correo, escribe cómo quedó y devuelve su estado."""
        try:
            correo = procesamiento.reprocesar(correo_id)
        except Correo.DoesNotExist:
            # Lo eliminaron entre la consulta y aquí.
            self.stdout.write(f"  correo {correo_id}: ya no existe")
            return "eliminado"
        except procesamiento.ErrorTransitorio as error:
            self.stdout.write(self.style.WARNING(
                f"  correo {correo_id}: queda pendiente, falló R2 o B2 ({error})"
            ))
            return Correo.Estado.PENDIENTE
        linea = f"  correo {correo.pk}: {correo.estado}"
        if correo.estado == Correo.Estado.ERROR:
            linea += f" ({correo.error_detalle})"
        self.stdout.write(linea)
        return correo.estado
