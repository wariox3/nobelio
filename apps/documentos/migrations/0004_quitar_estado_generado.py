"""Quita el estado `generado` ("XML generado").

Era un paso intermedio de `generar_y_firmar` —XML generado, todavía sin
firmar— que solo existía en memoria: el documento se guardaba ya firmado. Desde
que el XML pasó al almacenamiento de objetos (commit 0291e76) nada lo asigna, y
el código lo trataba igual que `borrador`.

Antes de borrar la fila, lo que la tuviera pasa a `borrador`, que es lo que
significaba para la lógica. Las dos claves foráneas son `PROTECT`: sin ese paso,
un solo documento o nómina con `generado` haría fallar la migración. Hay que
depender de nómina para poder tocar sus filas.
"""
from django.db import migrations, models

GENERADO = "generado"
BORRADOR = "borrador"


def quitar(apps, schema_editor):
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    generado = DocumentoEstado.objects.filter(nombre=GENERADO).first()
    if generado is None:
        return
    borrador = DocumentoEstado.objects.get(nombre=BORRADOR)
    apps.get_model("documentos", "Documento").objects.filter(
        estado=generado
    ).update(estado=borrador)
    apps.get_model("nomina", "Nomina").objects.filter(
        estado=generado
    ).update(estado=borrador)
    generado.delete()


def reponer(apps, schema_editor):
    """Vuelve la fila; los registros que se pasaron a borrador se quedan así."""
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    DocumentoEstado.objects.update_or_create(
        nombre=GENERADO, defaults={"descripcion": "XML generado", "codigo": ""},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0003_datos_de_referencia"),
        ("nomina", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(quitar, reponer),
        migrations.AlterField(
            model_name="documentoestado",
            name="nombre",
            field=models.CharField(
                choices=[
                    ("borrador", "Borrador"),
                    ("firmado", "Firmado"),
                    ("enviado", "Enviado a la DIAN"),
                    ("aceptado", "Aceptado por la DIAN"),
                    ("rechazado", "Rechazado por la DIAN"),
                ],
                max_length=20,
                unique=True,
                verbose_name="nombre",
            ),
        ),
    ]
