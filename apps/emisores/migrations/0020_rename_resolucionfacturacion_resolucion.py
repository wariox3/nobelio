from django.db import migrations


class Migration(migrations.Migration):
    """Las resoluciones no son solo de facturación: también numeran el DS."""

    dependencies = [
        ('emisores', '0019_quitar_codigo_interface'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='ResolucionFacturacion',
            new_name='Resolucion',
        ),
        migrations.AlterModelTable(
            name='resolucion',
            table='emi_resolucion',
        ),
        migrations.AlterModelOptions(
            name='resolucion',
            options={
                'ordering': ['-fecha_resolucion'],
                'verbose_name': 'resolución',
                'verbose_name_plural': 'resoluciones',
            },
        ),
    ]
