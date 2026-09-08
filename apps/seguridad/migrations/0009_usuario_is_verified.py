"""Añade `is_verified` y da por verificados los usuarios que ya existían.

El campo nace en `False`, que es lo correcto para quien se registre a partir de
ahora. Pero los usuarios anteriores a esta migración los creó el staff a mano
(`createsuperuser`, `UsuarioViewSet`), no un formulario público: nunca hubo un
correo que confirmar, y dejarlos en `False` les cerraría el login de golpe
—incluido el superusuario—. Se marcan verificados de una vez.

La vuelta atrás no restaura el valor: al quitar la columna se pierde igual.
"""
from django.db import migrations, models


def marcar_existentes(apps, schema_editor):
    Usuario = apps.get_model("seguridad", "Usuario")
    Usuario.objects.update(is_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ('seguridad', '0008_remove_llaveapi_emisor'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='is_verified',
            field=models.BooleanField(default=False, help_text='El usuario confirmó su correo. Sin esto no puede iniciar sesión.', verbose_name='correo verificado'),
        ),
        migrations.RunPython(marcar_existentes, migrations.RunPython.noop),
    ]
