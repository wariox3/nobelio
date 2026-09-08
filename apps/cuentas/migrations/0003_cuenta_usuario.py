"""Da dueño a la cuenta: `Cuenta.usuario`, obligatorio.

El campo nace obligatorio, pero las cuentas que ya existen no tienen a quién
apuntar, así que la columna se añade en tres pasos: primero anulable, después
se rellena, y solo entonces se cierra a `NOT NULL`. Hacerlo de una vez obligaría
a inventar un valor por defecto que no existe.

A las cuentas huérfanas se les asigna el usuario de staff más antiguo, que es
quien las creó en la práctica: antes del registro público, las cuentas solo
salían de `crear_llave_api` o del CRUD de staff. Si no hay ningún usuario, la
migración se planta en vez de dejar el campo a medias: es una base sin
superusuario, y hay que crearlo antes.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def asignar_dueno(apps, schema_editor):
    Cuenta = apps.get_model("cuentas", "Cuenta")
    huerfanas = Cuenta.objects.filter(usuario__isnull=True)
    if not huerfanas.exists():
        return
    Usuario = apps.get_model("seguridad", "Usuario")
    dueno = (
        Usuario.objects.filter(is_staff=True).order_by("creado_en").first()
        or Usuario.objects.order_by("creado_en").first()
    )
    if dueno is None:
        raise RuntimeError(
            "Hay cuentas sin dueño y ningún usuario al que asignárselas. "
            "Crea el superusuario (`manage.py createsuperuser`) y vuelve a "
            "aplicar la migración."
        )
    huerfanas.update(usuario=dueno)


class Migration(migrations.Migration):

    dependencies = [
        ('cuentas', '0002_alter_cuenta_table'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cuenta',
            name='usuario',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='cuentas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuario',
            ),
        ),
        migrations.RunPython(asignar_dueno, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='cuenta',
            name='usuario',
            field=models.ForeignKey(
                help_text='Usuario dueño de la cuenta.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='cuentas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuario',
            ),
        ),
    ]
