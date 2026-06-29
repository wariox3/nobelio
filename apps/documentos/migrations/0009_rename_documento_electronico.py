from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0008_documentoelectronico_track_id"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="DocumentoElectronico",
            new_name="Documento",
        ),
        migrations.AlterModelTable(
            name="documento",
            table="doc_documento",
        ),
    ]
