from django.db import migrations, models
from django.db.models import Max


def rellenar_consecutivo_actual(apps, schema_editor):
    """El siguiente al mayor consecutivo ya usado, o el primero del rango."""
    Resolucion = apps.get_model("emisores", "Resolucion")
    Documento = apps.get_model("documentos", "Documento")
    for resolucion in Resolucion.objects.all():
        usado = Documento.objects.filter(resolucion=resolucion).aggregate(
            maximo=Max("consecutivo")
        )["maximo"]
        resolucion.consecutivo_actual = (
            resolucion.rango_desde if usado is None else usado + 1
        )
        resolucion.save(update_fields=["consecutivo_actual"])


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0007_documento_notificacion'),
        ('emisores', '0012_eventos_radian'),
    ]

    operations = [
        migrations.AddField(
            model_name='resolucion',
            name='consecutivo_actual',
            field=models.PositiveBigIntegerField(blank=True, null=True, verbose_name='consecutivo actual'),
        ),
        migrations.RunPython(rellenar_consecutivo_actual, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='resolucion',
            name='consecutivo_actual',
            field=models.PositiveBigIntegerField(blank=True, help_text='Siguiente consecutivo a usar. Vacío toma el rango desde.', verbose_name='consecutivo actual'),
        ),
    ]
