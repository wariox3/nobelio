"""La llave de API pasa a tener alcance de cuenta y el usuario, de emisores.

Dos cambios de alcance que van juntos:

- ``LlaveApi.emisor`` (obligatorio) → ``LlaveApi.cuenta`` (obligatorio) con
  ``emisor`` opcional. Una integración opera con una sola credencial sobre
  todos los emisores de su cuenta. Las llaves existentes se reasignan a la
  cuenta de su emisor y conservan el emisor, así que su alcance no cambia.
- ``Usuario.emisores``: el alcance de una persona ya no es su cuenta (que
  agrupa los emisores de muchos clientes distintos), sino los emisores que
  tenga asignados.
"""
import django.db.models.deletion
from django.db import migrations, models


def llaves_a_cuenta_del_emisor(apps, schema_editor):
    """Cada llave hereda la cuenta de su emisor actual."""
    LlaveApi = apps.get_model("seguridad", "LlaveApi")
    for llave in LlaveApi.objects.select_related("emisor").all():
        llave.cuenta_id = llave.emisor.cuenta_id
        llave.save(update_fields=["cuenta"])


def cuenta_a_emisor(apps, schema_editor):
    """Marcha atrás: sin la cuenta, el emisor vuelve a ser obligatorio."""
    LlaveApi = apps.get_model("seguridad", "LlaveApi")
    LlaveApi.objects.filter(emisor__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0001_initial"),
        ("emisores", "0003_alter_certificadodigital_table_alter_emisor_table_and_more"),
        ("seguridad", "0005_alter_llaveapi_table"),
    ]

    operations = [
        # 1. Se añade nullable para poder rellenarla a partir del emisor.
        migrations.AddField(
            model_name="llaveapi",
            name="cuenta",
            field=models.ForeignKey(
                null=True,
                help_text="Alcance de la llave: todos los emisores de esta cuenta.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llaves_api",
                to="cuentas.cuenta",
                verbose_name="cuenta",
            ),
        ),
        migrations.RunPython(llaves_a_cuenta_del_emisor, cuenta_a_emisor),
        # 2. Ya con datos, pasa a obligatoria.
        migrations.AlterField(
            model_name="llaveapi",
            name="cuenta",
            field=models.ForeignKey(
                help_text="Alcance de la llave: todos los emisores de esta cuenta.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llaves_api",
                to="cuentas.cuenta",
                verbose_name="cuenta",
            ),
        ),
        # 3. El emisor queda como restricción opcional.
        migrations.AlterField(
            model_name="llaveapi",
            name="emisor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text="Opcional: restringe la llave a un único emisor de la cuenta.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llaves_api",
                to="emisores.emisor",
                verbose_name="emisor",
            ),
        ),
        migrations.AddField(
            model_name="usuario",
            name="emisores",
            field=models.ManyToManyField(
                blank=True,
                help_text="Emisores cuyos datos puede consultar y operar el usuario.",
                related_name="usuarios",
                to="emisores.emisor",
                verbose_name="emisores",
            ),
        ),
    ]
