"""Recalcula el DV de los emisores ya guardados.

Hasta ahora el dígito de verificación se guardaba tal como llegaba (del RUES o
tecleado), y un valor mal hacía que la DIAN rechazara todos los documentos del
emisor (CAJ24, CAK24 y la del Prestador de Servicios). Desde este punto lo
calcula el modelo; esta migración arregla lo que ya estaba en la base.
"""
from django.db import migrations

from apps.utilidades.nit import CODIGO_NIT, digito_verificacion


def recalcular(apps, schema_editor):
    Emisor = apps.get_model("emisores", "Emisor")
    for emisor in Emisor.objects.select_related("tipo_identificacion").iterator():
        tipo = emisor.tipo_identificacion
        dv = (
            digito_verificacion(emisor.numero_identificacion)
            if tipo and tipo.codigo == CODIGO_NIT
            else ""
        )
        if dv != emisor.digito_verificacion:
            emisor.digito_verificacion = dv
            emisor.save(update_fields=["digito_verificacion"])


class Migration(migrations.Migration):
    dependencies = [
        ("emisores", "0014_emisor_habilitado_facturacion"),
        ("catalogos", "0001_initial"),
    ]

    operations = [
        # Sin reversa: el valor anterior era el erróneo, no hay nada que restaurar.
        migrations.RunPython(recalcular, migrations.RunPython.noop),
    ]
