"""Un certificado por emisor, y su resumen en el emisor.

El certificado pasa de ForeignKey con bandera ``activo`` a OneToOne: cargar uno
nuevo ya no jubila al anterior, hay que borrarlo. Esta migración tiene que
dejar la tabla en condiciones de aceptar el índice único, así que antes de
cambiar la columna se lleva por delante lo que sobra —los jubilados y, si algún
emisor llegara con dos vigentes, el menos reciente— junto con sus .p12 del
almacenamiento; si no, el archivo se queda en el bucket sin fila que lo nombre.

El orden de las operaciones no es cosmético: el borrado necesita leer ``activo``
para saber cuál conservar, así que va antes de quitar la columna.
"""
import django.db.models.deletion
from django.db import migrations, models


def _borrar_archivo(cert):
    """Quita el .p12 del almacenamiento. Un fallo no puede tumbar el despliegue."""
    nombre = cert.archivo.name
    if not nombre:
        return
    try:
        cert.archivo.storage.delete(nombre)
    except Exception as exc:  # B2 caído, credencial caduca, permisos...
        # Queda huérfano en el bucket; se dice cuál para poder limpiarlo a mano.
        print(f"  ! no se pudo borrar '{nombre}' del almacenamiento: {exc}")


def dejar_uno_por_emisor(apps, schema_editor):
    Certificado = apps.get_model("emisores", "Certificado")
    Emisor = apps.get_model("emisores", "Emisor")

    # Los jubilados no los lee nadie: el código solo buscaba `activo=True`.
    sobrantes = list(Certificado.objects.filter(activo=False))

    # Dos vigentes del mismo emisor no deberían existir, pero el índice único
    # aún no está y la migración no puede reventar por ello: gana el más nuevo.
    emisores = (
        Certificado.objects.filter(activo=True)
        .values_list("emisor_id", flat=True)
        .distinct()
    )
    for emisor_id in emisores:
        vigentes = list(
            Certificado.objects.filter(emisor_id=emisor_id, activo=True)
            .order_by("-creado_en")
        )
        sobrantes.extend(vigentes[1:])

    for cert in sobrantes:
        _borrar_archivo(cert)
        cert.delete()

    # El resumen que a partir de ahora mantiene `Certificado._sincronizar_resumen`.
    for cert in Certificado.objects.all():
        Emisor.objects.filter(pk=cert.emisor_id).update(
            certificado_activo=True, certificado_vence=cert.vigente_hasta,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('emisores', '0003_remove_emisor_nombre_comercial'),
    ]

    operations = [
        migrations.AddField(
            model_name='emisor',
            name='certificado_activo',
            field=models.BooleanField(default=False, help_text='Si el emisor tiene un .p12 cargado. No dice que esté vigente: eso lo responde `certificado_vence`, porque una bandera que dependiera de la fecha quedaría mintiendo el día que el certificado venza, sin que nadie haya escrito nada.', verbose_name='tiene certificado digital'),
        ),
        migrations.AddField(
            model_name='emisor',
            name='certificado_vence',
            field=models.DateField(blank=True, help_text='Fin de la vigencia del .p12 cargado, tal y como viene dentro del propio certificado. Nulo si no hay certificado.', null=True, verbose_name='el certificado vence el'),
        ),
        # Sin vuelta atrás: los .p12 borrados no se reponen desde aquí. El
        # esquema sí revierte, que es lo que un rollback necesita.
        migrations.RunPython(dejar_uno_por_emisor, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='certificado',
            name='activo',
        ),
        migrations.AlterField(
            model_name='certificado',
            name='emisor',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='certificado', to='emisores.emisor', verbose_name='emisor'),
        ),
    ]
