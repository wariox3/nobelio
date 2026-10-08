"""Verifica contra la DIAN los documentos recibidos que no lo están.

Para los que se registraron antes de que existiera la verificación, los que
quedaron en ``error`` (la DIAN no respondió más allá de los reintentos) y los
``no_verificables`` (el emisor no tenía certificado y ya lo cargó). Corre aquí
mismo, no en Celery, y muestra cómo quedó cada uno.

Ejemplo::

    # Uno.
    python manage.py verificar_documentos --id <uuid>

    # Todos los pendientes, en error o no verificables.
    python manage.py verificar_documentos --todos
"""
from django.core.management.base import BaseCommand, CommandError

from apps.recepcion import verificacion
from apps.recepcion.models import Documento, EstadoVerificacion

POR_VERIFICAR = [
    EstadoVerificacion.PENDIENTE,
    EstadoVerificacion.ERROR,
    EstadoVerificacion.NO_VERIFICABLE,
]


class Command(BaseCommand):
    help = "Verifica contra la DIAN los documentos recibidos pendientes, en error o no verificables."

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument("--id", help="El documento a verificar (su UUID).")
        grupo.add_argument(
            "--todos", action="store_true",
            help="Todos los pendientes, en error o no verificables.",
        )

    def handle(self, *args, **opciones):
        documentos = Documento.objects.select_related("emisor")
        if opciones["id"]:
            documentos = documentos.filter(pk=opciones["id"])
            if not documentos.exists():
                raise CommandError(f"No existe el documento {opciones['id']}.")
        else:
            documentos = documentos.filter(verificacion_estado__in=POR_VERIFICAR)
        conteo = {}
        for documento in documentos.order_by("creado_en"):
            try:
                verificacion.verificar(documento)
            except verificacion.ErrorTransitorio as error:
                verificacion.marcar_error(documento, str(error))
            estado = documento.get_verificacion_estado_display()
            conteo[estado] = conteo.get(estado, 0) + 1
            self.stdout.write(f"{documento.numero} ({documento.pk}): {estado}")
        if not conteo:
            self.stdout.write("No hay documentos por verificar.")
            return
        resumen = ", ".join(f"{estado}: {n}" for estado, n in sorted(conteo.items()))
        self.stdout.write(self.style.SUCCESS(f"Listo. {resumen}."))
