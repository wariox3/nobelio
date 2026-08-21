"""Recalcula el DV de los adquirientes ya guardados. Ver la 0015 de emisores."""
from django.db import migrations

from apps.utilidades.nit import CODIGO_NIT, digito_verificacion


def recalcular(apps, schema_editor):
    Adquiriente = apps.get_model("documentos", "Adquiriente")
    for adq in Adquiriente.objects.select_related("tipo_identificacion").iterator():
        tipo = adq.tipo_identificacion
        dv = (
            digito_verificacion(adq.numero_identificacion)
            if tipo and tipo.codigo == CODIGO_NIT
            else ""
        )
        if dv != adq.digito_verificacion:
            adq.digito_verificacion = dv
            adq.save(update_fields=["digito_verificacion"])


class Migration(migrations.Migration):
    dependencies = [
        ("documentos", "0023_documento_ambiente"),
        ("catalogos", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(recalcular, migrations.RunPython.noop),
    ]
