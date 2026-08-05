"""El adquiriente pasa a pertenecer a un emisor.

Hasta ahora la tabla era global: un mismo NIT era una única fila compartida por
todos los emisores de la plataforma, así que la cartera de clientes de una
cuenta era visible (y editable) desde otra, y dos emisores no podían registrar
al mismo cliente con sus propios datos de contacto.

La reasignación reparte cada adquiriente entre los emisores que lo facturaron,
duplicando la fila cuando lo usó más de uno. Los adquirientes que no tienen
ningún documento no se pueden atribuir automáticamente: si hay un solo emisor
en la base de datos se les asigna ese, y si hay varios la migración se detiene
para que se resuelva a mano en lugar de adivinar.
"""
import django.db.models.deletion
from django.db import migrations, models

MENSAJE_AMBIGUO = (
    "No se puede migrar: hay {n} adquirientes sin documentos y {e} emisores, "
    "así que no hay forma de saber a quién pertenecen. Asígnalos a mano "
    "(añadiendo la columna emisor_id a mano o borrando las filas que sobren) "
    "y vuelve a ejecutar la migración."
)


def _clonar(Adquiriente, original, emisor_id):
    """Copia el adquiriente para otro emisor, con sus responsabilidades."""
    clon = Adquiriente.objects.get(pk=original.pk)
    clon.pk = None
    clon.emisor_id = emisor_id
    clon.save()
    clon.responsabilidades.set(original.responsabilidades.all())
    return clon


def repartir_por_emisor(apps, schema_editor):
    Adquiriente = apps.get_model("documentos", "Adquiriente")
    Documento = apps.get_model("documentos", "Documento")
    Emisor = apps.get_model("emisores", "Emisor")

    for adquiriente in Adquiriente.objects.all():
        emisores = list(
            Documento.objects.filter(adquiriente=adquiriente)
            .values_list("emisor_id", flat=True)
            .distinct()
        )
        if not emisores:
            continue
        # El primero se queda con la fila original; el resto la clonan y se
        # llevan sus propios documentos.
        adquiriente.emisor_id = emisores[0]
        adquiriente.save(update_fields=["emisor"])
        for emisor_id in emisores[1:]:
            clon = _clonar(Adquiriente, adquiriente, emisor_id)
            Documento.objects.filter(
                adquiriente=adquiriente, emisor_id=emisor_id
            ).update(adquiriente=clon)

    huerfanos = Adquiriente.objects.filter(emisor__isnull=True)
    total_huerfanos = huerfanos.count()
    if not total_huerfanos:
        return
    emisores = list(Emisor.objects.values_list("pk", flat=True)[:2])
    if len(emisores) != 1:
        raise RuntimeError(
            MENSAJE_AMBIGUO.format(n=total_huerfanos, e=len(emisores))
        )
    huerfanos.update(emisor_id=emisores[0])


def quitar_emisor(apps, schema_editor):
    """Marcha atrás: se conserva una fila por NIT, se descartan los duplicados."""
    Adquiriente = apps.get_model("documentos", "Adquiriente")
    vistos = set()
    for adquiriente in Adquiriente.objects.order_by("pk"):
        clave = (adquiriente.tipo_identificacion_id, adquiriente.numero_identificacion)
        if clave in vistos:
            adquiriente.delete()
        else:
            vistos.add(clave)


class Migration(migrations.Migration):

    dependencies = [
        ("emisores", "0003_alter_certificadodigital_table_alter_emisor_table_and_more"),
        ("documentos", "0020_documentoestado_nombre"),
    ]

    operations = [
        # La unicidad global estorba para clonar: se quita antes de repartir.
        migrations.RemoveConstraint(
            model_name="adquiriente",
            name="adquiriente_identificacion_unica",
        ),
        migrations.AddField(
            model_name="adquiriente",
            name="emisor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="adquirientes",
                to="emisores.emisor",
                verbose_name="emisor",
            ),
        ),
        migrations.RunPython(repartir_por_emisor, quitar_emisor),
        migrations.AlterField(
            model_name="adquiriente",
            name="emisor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="adquirientes",
                to="emisores.emisor",
                verbose_name="emisor",
            ),
        ),
        migrations.AddConstraint(
            model_name="adquiriente",
            constraint=models.UniqueConstraint(
                fields=("emisor", "tipo_identificacion", "numero_identificacion"),
                name="adquiriente_identificacion_unica_por_emisor",
            ),
        ),
    ]
