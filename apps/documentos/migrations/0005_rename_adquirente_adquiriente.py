from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0004_alter_documentoelectronico_table_and_more"),
    ]

    operations = [
        # Renombrar el modelo (conserva la tabla y sus FKs) y luego la tabla
        # física y la columna FK en el documento.
        migrations.RenameModel(
            old_name="Adquirente",
            new_name="Adquiriente",
        ),
        migrations.AlterModelTable(
            name="adquiriente",
            table="doc_adquiriente",
        ),
        migrations.RenameField(
            model_name="documentoelectronico",
            old_name="adquirente",
            new_name="adquiriente",
        ),
    ]
