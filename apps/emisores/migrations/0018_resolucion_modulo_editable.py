from django.db import migrations, models

# Ids del catálogo de tipos de factura: documento soporte (05) y su nota de
# ajuste (95).
TIPOS_FACTURA_DE_COMPRAS = (7, 8)


def rellenar_modulo(apps, schema_editor):
    """Lo mismo que calculaba la columna generada que se quita."""
    Resolucion = apps.get_model("emisores", "Resolucion")
    Resolucion.objects.filter(
        tipo_factura_id__in=TIPOS_FACTURA_DE_COMPRAS
    ).update(modulo="compras")
    Resolucion.objects.exclude(
        tipo_factura_id__in=TIPOS_FACTURA_DE_COMPRAS
    ).update(modulo="facturacion")


class Migration(migrations.Migration):

    dependencies = [
        ('emisores', '0017_resolucion_modulo'),
    ]

    operations = [
        # Una columna generada no se convierte en una normal: se quita y se
        # vuelve a crear.
        migrations.RemoveField(
            model_name='resolucion',
            name='modulo',
        ),
        migrations.AddField(
            model_name='resolucion',
            name='modulo',
            field=models.CharField(blank=True, choices=[('facturacion', 'Facturación'), ('nomina', 'Nómina'), ('compras', 'Compras')], help_text='facturacion, nomina o compras. Vacío toma el que corresponde al tipo de factura.', max_length=15, verbose_name='módulo'),
        ),
        migrations.RunPython(rellenar_modulo, migrations.RunPython.noop),
    ]
