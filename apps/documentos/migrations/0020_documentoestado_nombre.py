from django.db import migrations, models

_CHOICES = [
    ("borrador", "Borrador"),
    ("generado", "XML generado"),
    ("firmado", "Firmado"),
    ("enviado", "Enviado a la DIAN"),
    ("aceptado", "Aceptado por la DIAN"),
    ("rechazado", "Rechazado por la DIAN"),
]


def codigo_a_nombre(apps, schema_editor):
    """El identificador (borrador/aceptado…) pasa de ``codigo`` a ``nombre``;
    ``codigo`` queda libre para el código de la DIAN (vacío)."""
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    for e in DocumentoEstado.objects.all():
        e.nombre = e.codigo
        e.codigo = ""
        e.save(update_fields=["nombre", "codigo"])


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0019_documento_fecha_validacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentoestado",
            name="nombre",
            field=models.CharField(
                blank=True, default="", max_length=20, choices=_CHOICES,
                verbose_name="nombre",
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="documentoestado",
            name="codigo",
            field=models.CharField(
                blank=True, max_length=10, verbose_name="código DIAN",
                help_text="Código de la DIAN para el estado (opcional).",
            ),
        ),
        migrations.RunPython(codigo_a_nombre, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="documentoestado",
            name="nombre",
            field=models.CharField(
                max_length=20, unique=True, choices=_CHOICES, verbose_name="nombre",
            ),
        ),
    ]
