"""Siembra los dos tipos de nota de ajuste al documento equivalente."""
from django.db import migrations, models

TIPOS = [
    ("nota_ajuste_de_credito",
     "Nota de ajuste crédito al documento equivalente", "94"),
    # El UBL DebitNote no define elemento de tipo, así que esta nota no emite
    # ningún código en el XML y su `codigo_dian` se queda vacío a propósito:
    # inventarle uno sería documentar algo que el anexo no dice.
    ("nota_ajuste_de_debito",
     "Nota de ajuste débito al documento equivalente", ""),
]


def crear_tipos(apps, schema_editor):
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    for codigo, nombre, codigo_dian in TIPOS:
        DocumentoTipo.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "codigo_dian": codigo_dian},
        )


def borrar_tipos(apps, schema_editor):
    """Solo los que nadie use: un tipo con documentos emitidos no se borra."""
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    DocumentoTipo.objects.filter(
        codigo__in=[t[0] for t in TIPOS], documentos__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0041_consecutivo_archivo_de"),
    ]

    operations = [
        migrations.RunPython(crear_tipos, borrar_tipos),
    ]
