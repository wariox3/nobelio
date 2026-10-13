from django.db import migrations, models

PRODUCCION = 1
PRUEBAS = 2

# La resolución del Set de Pruebas de la DIAN, la misma para todos los emisores
# (facturación con prefijo SETP y P.O.S. con EPOS).
NUMERO_SET_PRUEBAS = "18760000001"


def rellenar_ambiente(apps, schema_editor):
    """Habilitación para la del Set de Pruebas; producción para las demás.

    Es lo único que se puede deducir de lo ya guardado: de las importadas de la
    DIAN no quedó registro del ambiente con el que se consultaron.
    """
    Resolucion = apps.get_model("emisores", "Resolucion")
    Resolucion.objects.filter(numero_resolucion=NUMERO_SET_PRUEBAS).update(
        ambiente=PRUEBAS
    )
    Resolucion.objects.exclude(numero_resolucion=NUMERO_SET_PRUEBAS).update(
        ambiente=PRODUCCION
    )


class Migration(migrations.Migration):

    dependencies = [
        ('emisores', '0015_webhook_de_cuenta'),
    ]

    operations = [
        migrations.AddField(
            model_name='resolucion',
            name='ambiente',
            field=models.PositiveSmallIntegerField(choices=[(1, 'Producción'), (2, 'Habilitación')], null=True, verbose_name='ambiente DIAN'),
        ),
        migrations.RunPython(rellenar_ambiente, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='resolucion',
            name='ambiente',
            field=models.PositiveSmallIntegerField(choices=[(1, 'Producción'), (2, 'Habilitación')], help_text='1 producción, 2 habilitación. Lo pone la siembra del Set de Pruebas y la importación desde la DIAN; en el alta manual lo indica quien la crea.', verbose_name='ambiente DIAN'),
        ),
    ]
