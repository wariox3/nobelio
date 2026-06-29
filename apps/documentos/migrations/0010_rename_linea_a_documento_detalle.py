from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0009_rename_documento_electronico"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="LineaDocumento",
            new_name="DocumentoDetalle",
        ),
        migrations.RenameModel(
            old_name="ImpuestoLinea",
            new_name="DocumentoDetalleImpuesto",
        ),
        migrations.AlterModelTable(
            name="documentodetalle",
            table="doc_documento_detalle",
        ),
        migrations.AlterModelTable(
            name="documentodetalleimpuesto",
            table="doc_documento_detalle_impuesto",
        ),
        migrations.RenameField(
            model_name="documentodetalleimpuesto",
            old_name="linea",
            new_name="detalle",
        ),
    ]
