"""Consulta en la DIAN los eventos RADIAN de una factura recibida (GetStatusEvent).

No registra nada ni guarda nada en la base. Deja la respuesta cruda y el
ApplicationResponse con los eventos en ``--salida``, para confirmar la forma de
esa respuesta, que ``docs/recepcion.md`` marca con ⚠ (R3).

    python manage.py consultar_eventos --id <uuid>

Consulta en el ambiente del documento, con el certificado de su emisor.
"""
import pathlib

from django.core.management.base import BaseCommand, CommandError

from apps.dian import soap
from apps.dian.servicios import construir_cliente_emisor
from apps.nucleo.models import Ambiente
from apps.recepcion.models import Documento


class Command(BaseCommand):
    help = "Consulta en la DIAN los eventos RADIAN de una factura recibida (GetStatusEvent)."

    def add_arguments(self, parser):
        parser.add_argument("--id", required=True, help="El documento recibido (su UUID).")
        parser.add_argument(
            "--salida", default="capturas/eventos",
            help="Dónde dejar las respuestas (por defecto capturas/eventos).",
        )

    def handle(self, *args, **opciones):
        documento = (
            Documento.objects.select_related("emisor").filter(pk=opciones["id"]).first()
        )
        if documento is None:
            raise CommandError(f"No existe el documento {opciones['id']}.")
        salida = pathlib.Path(opciones["salida"])
        salida.mkdir(parents=True, exist_ok=True)
        ambiente = documento.ambiente or Ambiente.PRODUCCION

        resultado = construir_cliente_emisor(documento.emisor, ambiente).consultar_eventos(
            documento.cufe_cude,
        )
        respuesta = resultado.respuesta
        base = salida / f"GetStatusEvent-{documento.numero}"
        pathlib.Path(f"{base}-respuesta.xml").write_text(respuesta.xml_crudo)
        if application_response := soap.extraer_application_response(respuesta.xml_crudo):
            pathlib.Path(f"{base}-eventos.xml").write_bytes(application_response)

        estilo = self.style.SUCCESS if respuesta.es_valido else self.style.ERROR
        self.stdout.write(estilo(
            f"GetStatusEvent: {respuesta.codigo_estado} {respuesta.descripcion_estado}"
        ))
        for error in respuesta.errores:
            self.stdout.write(f"  {error}")
        for evento in resultado.eventos:
            self.stdout.write(f"  evento {evento.codigo}: {evento.descripcion}")
        if not resultado.eventos:
            self.stdout.write("  (sin eventos leídos)")
        self.stdout.write(f"Respuesta cruda en {base}-respuesta.xml")
