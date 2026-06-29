"""
Emite un documento electrónico: lo genera, firma y (opcionalmente) lo envía a
la DIAN. Útil para el proceso de habilitación contra el Set de Pruebas.

    python manage.py emitir_documento <id>            # solo genera y firma
    python manage.py emitir_documento <id> --enviar   # firma y envía a la DIAN
"""
from django.core.management.base import BaseCommand, CommandError

from apps.dian import servicios
from apps.documentos.models import Documento


class Command(BaseCommand):
    help = "Genera, firma y opcionalmente envía un documento a la DIAN."

    def add_arguments(self, parser):
        parser.add_argument("id", help="UUID del documento a emitir.")
        parser.add_argument(
            "--enviar", action="store_true",
            help="Envía el documento a la DIAN tras firmarlo (Set de Pruebas en habilitación).",
        )

    def handle(self, *args, **opciones):
        try:
            documento = Documento.objects.get(pk=opciones["id"])
        except (Documento.DoesNotExist, ValueError) as exc:
            raise CommandError(f"No existe el documento {opciones['id']}") from exc

        try:
            servicios.generar_y_firmar(documento)
        except servicios.ErrorEmision as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Firmado: {documento.numero}  CUFE/CUDE={documento.cufe_cude}"
        ))

        if opciones["enviar"]:
            self.stdout.write("Enviando a la DIAN...")
            try:
                respuesta = servicios.enviar_a_dian(documento)
            except servicios.ErrorEmision as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                f"  estado:    {documento.estado}\n"
                f"  trackId:   {respuesta.track_id}\n"
                f"  válido:    {respuesta.es_valido}\n"
                f"  código:    {respuesta.codigo_estado}\n"
                f"  detalle:   {respuesta.descripcion_estado}"
            )
            for error in respuesta.errores:
                self.stdout.write(self.style.WARNING(f"  - {error}"))
