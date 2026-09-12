"""Un software DIAN por emisor y operación; fuera la bandera ``activo``.

Antes, registrar un software jubilaba (``activo=False``) al anterior del mismo
tipo y lo dejaba en la tabla. Nadie leía esas filas —el pipeline pedía siempre
el activo *de su tipo*— y cada una guardaba un PIN vivo, que es lo que entra en
el SoftwareSecurityCode y en el CUDE/CUNE.

Para que entre el índice único hay que dejar una sola fila por (emisor, tipo),
y elegirla necesita leer ``activo``: por eso el borrado va antes de quitar la
columna. Gana el activo más reciente y, si ninguno lo está, el más reciente.
"""
from django.db import migrations, models


def dejar_uno_por_tipo(apps, schema_editor):
    SoftwareDian = apps.get_model("emisores", "SoftwareDian")

    grupos = (
        SoftwareDian.objects.values_list("emisor_id", "tipo").distinct()
    )
    sobrantes = []
    for emisor_id, tipo in grupos:
        filas = list(
            SoftwareDian.objects.filter(emisor_id=emisor_id, tipo=tipo)
            # `activo` primero y, a igualdad, el más nuevo: es el que el
            # pipeline venía usando, así que conservarlo no cambia nada de lo
            # que el emisor emite hoy.
            .order_by("-activo", "-creado_en")
        )
        sobrantes.extend(f.pk for f in filas[1:])

    if sobrantes:
        print(f"  · se descartan {len(sobrantes)} software(s) jubilado(s)")
        SoftwareDian.objects.filter(pk__in=sobrantes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('emisores', '0004_certificado_unico_por_emisor'),
    ]

    operations = [
        # Sin vuelta atrás: las filas jubiladas no se reponen. El esquema sí
        # revierte, que es lo que un rollback necesita.
        migrations.RunPython(dejar_uno_por_tipo, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='softwaredian',
            name='activo',
        ),
        migrations.AddConstraint(
            model_name='softwaredian',
            constraint=models.UniqueConstraint(fields=('emisor', 'tipo'), name='emi_software_unico_por_tipo'),
        ),
    ]
