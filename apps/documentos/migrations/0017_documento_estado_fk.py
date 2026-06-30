import django.db.models.deletion
from django.db import migrations, models

ESTADOS = [
    ("borrador", "Borrador"),
    ("generado", "XML generado"),
    ("firmado", "Firmado"),
    ("enviado", "Enviado a la DIAN"),
    ("aceptado", "Aceptado por la DIAN"),
    ("rechazado", "Rechazado por la DIAN"),
]


def sembrar_estados(apps, schema_editor):
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    for codigo, descripcion in ESTADOS:
        DocumentoEstado.objects.update_or_create(
            codigo=codigo, defaults={"descripcion": descripcion}
        )


def migrar_estado_a_fk(apps, schema_editor):
    Documento = apps.get_model("documentos", "Documento")
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    mapa = {e.codigo: e for e in DocumentoEstado.objects.all()}
    for doc in Documento.objects.all():
        doc.estado = mapa[doc.estado_anterior]
        doc.save(update_fields=["estado"])


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0016_remove_documento_errores_documentoerror"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentoEstado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("creado_en", models.DateTimeField(auto_now_add=True, verbose_name="creado en")),
                ("actualizado_en", models.DateTimeField(auto_now=True, verbose_name="actualizado en")),
                ("codigo", models.CharField(choices=[("borrador", "Borrador"), ("generado", "XML generado"), ("firmado", "Firmado"), ("enviado", "Enviado a la DIAN"), ("aceptado", "Aceptado por la DIAN"), ("rechazado", "Rechazado por la DIAN")], max_length=20, unique=True, verbose_name="código")),
                ("descripcion", models.CharField(max_length=100, verbose_name="descripción")),
                ("activo", models.BooleanField(default=True, verbose_name="activo")),
            ],
            options={
                "verbose_name": "estado de documento",
                "verbose_name_plural": "estados de documento",
                "db_table": "doc_documento_estado",
                "ordering": ["id"],
            },
        ),
        migrations.RunPython(sembrar_estados, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="documento", old_name="estado", new_name="estado_anterior",
        ),
        migrations.AddField(
            model_name="documento",
            name="estado",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos",
                to="documentos.documentoestado",
                verbose_name="estado",
            ),
        ),
        migrations.RunPython(migrar_estado_a_fk, migrations.RunPython.noop),
        migrations.RemoveField(model_name="documento", name="estado_anterior"),
        migrations.AlterField(
            model_name="documento",
            name="estado",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos",
                to="documentos.documentoestado",
                verbose_name="estado",
            ),
        ),
        migrations.RemoveField(model_name="documento", name="codigo_estado"),
        migrations.RemoveField(model_name="documento", name="descripcion_estado"),
    ]
