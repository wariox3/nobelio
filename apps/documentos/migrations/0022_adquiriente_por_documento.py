"""El adquiriente deja de ser cartera del emisor y pasa a ser del documento.

Los datos del receptor se piden en cada documento, así que cada fila cuelga de
un documento (1:1) en vez del emisor. Las filas existentes se clonan una por
documento: dos facturas al mismo NIT dejan de compartir fila, que es justo lo
que permite que cada documento conserve al receptor tal como se facturó.
"""
from django.db import migrations, models
import django.db.models.deletion


def adquirientes_a_los_documentos(apps, schema_editor):
    Adquiriente = apps.get_model("documentos", "Adquiriente")
    Documento = apps.get_model("documentos", "Documento")

    copiables = [
        "razon_social", "numero_identificacion", "digito_verificacion",
        "direccion", "telefono", "correo", "tipo_identificacion_id",
        "tipo_organizacion_id", "pais_id", "departamento_id", "municipio_id",
    ]
    for documento in Documento.objects.select_related("adquiriente"):
        original = documento.adquiriente
        copia = Adquiriente.objects.create(
            documento_id=documento.pk,
            **{campo: getattr(original, campo) for campo in copiables},
        )
        copia.responsabilidades.set(original.responsabilidades.all())

    # Los originales ya no cuelgan de nada: su emisor está a punto de irse.
    Adquiriente.objects.filter(documento__isnull=True).delete()


def sin_vuelta_atras(apps, schema_editor):
    raise migrations.exceptions.IrreversibleError(
        "No se puede reconstruir la cartera de adquirientes por emisor."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0021_adquiriente_emisor"),
    ]

    operations = [
        migrations.AddField(
            model_name="adquiriente",
            name="documento",
            # Sin `related_name` todavía: `Documento.adquiriente` sigue siendo la
            # FK vieja hasta que se elimine, y dos accesores con el mismo nombre
            # se pisarían mientras corre la copia.
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="documentos.documento",
                verbose_name="documento",
            ),
        ),
        migrations.RunPython(adquirientes_a_los_documentos, sin_vuelta_atras),
        migrations.RemoveConstraint(
            model_name="adquiriente",
            name="adquiriente_identificacion_unica_por_emisor",
        ),
        migrations.RemoveField(model_name="adquiriente", name="emisor"),
        migrations.RemoveField(model_name="documento", name="adquiriente"),
        migrations.AlterField(
            model_name="adquiriente",
            name="documento",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="adquiriente",
                to="documentos.documento",
                verbose_name="documento",
            ),
        ),
    ]
