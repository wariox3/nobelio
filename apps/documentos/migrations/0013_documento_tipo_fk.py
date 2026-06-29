import django.db.models.deletion
from django.db import migrations, models

TIPOS = [
    ("factura_venta", "Factura de venta", "01"),
    ("nota_credito", "Nota crédito", "91"),
    ("nota_debito", "Nota débito", "92"),
    ("documento_soporte", "Documento soporte", "05"),
    ("nomina", "Nómina electrónica", ""),
]


def poblar_tipos(apps, schema_editor):
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    for codigo, nombre, codigo_dian in TIPOS:
        DocumentoTipo.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "codigo_dian": codigo_dian},
        )


def migrar_documentos(apps, schema_editor):
    Documento = apps.get_model("documentos", "Documento")
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    tipos = {t.codigo: t for t in DocumentoTipo.objects.all()}
    for doc in Documento.objects.all():
        doc.documento_tipo = tipos[doc.tipo]
        doc.save(update_fields=["documento_tipo"])


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0012_documentotipo"),
    ]

    operations = [
        migrations.RunPython(poblar_tipos, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="documento",
            name="documento_numero_unico_por_emisor",
        ),
        migrations.AddField(
            model_name="documento",
            name="documento_tipo",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos",
                to="documentos.documentotipo",
                verbose_name="tipo de documento",
            ),
        ),
        migrations.RunPython(migrar_documentos, migrations.RunPython.noop),
        migrations.RemoveField(model_name="documento", name="tipo"),
        migrations.AlterField(
            model_name="documento",
            name="documento_tipo",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos",
                to="documentos.documentotipo",
                verbose_name="tipo de documento",
            ),
        ),
        migrations.AddConstraint(
            model_name="documento",
            constraint=models.UniqueConstraint(
                fields=["emisor", "prefijo", "consecutivo", "documento_tipo"],
                name="documento_numero_unico_por_emisor",
            ),
        ),
    ]
