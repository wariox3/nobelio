import apps.documentos.models.documento
import apps.utilidades.almacenamiento
from django.db import migrations, models


def migrar_artefactos_a_storage(apps, schema_editor):
    """Mueve xml_firmado/respuesta_dian (TextField) a archivos en B2 y parsea la respuesta."""
    from django.core.files.base import ContentFile

    from apps.dian import soap

    Documento = apps.get_model("documentos", "Documento")
    for doc in Documento.objects.all():
        cambios = []
        if doc.xml_firmado:
            doc.xml_archivo.save(
                f"{doc.numero}.xml",
                ContentFile(doc.xml_firmado.encode("utf-8")),
                save=False,
            )
            cambios.append("xml_archivo")
        if doc.respuesta_dian:
            r = soap.RespuestaDian.desde_xml(doc.respuesta_dian)
            doc.respuesta_archivo.save(
                f"{doc.numero}.dian.xml",
                ContentFile(doc.respuesta_dian.encode("utf-8")),
                save=False,
            )
            doc.codigo_estado = r.codigo_estado
            doc.descripcion_estado = r.descripcion_estado
            doc.errores = r.errores
            cambios += ["respuesta_archivo", "codigo_estado", "descripcion_estado", "errores"]
        if cambios:
            doc.save(update_fields=cambios)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0014_alter_documento_numero'),
    ]

    operations = [
        migrations.AddField(
            model_name='documento',
            name='codigo_estado',
            field=models.CharField(blank=True, max_length=10, verbose_name='código de estado DIAN'),
        ),
        migrations.AddField(
            model_name='documento',
            name='descripcion_estado',
            field=models.CharField(blank=True, max_length=255, verbose_name='descripción de estado DIAN'),
        ),
        migrations.AddField(
            model_name='documento',
            name='errores',
            field=models.JSONField(blank=True, default=list, verbose_name='errores/notificaciones DIAN'),
        ),
        migrations.AddField(
            model_name='documento',
            name='respuesta_archivo',
            field=models.FileField(blank=True, storage=apps.utilidades.almacenamiento.almacenamiento_backblaze, upload_to=apps.documentos.models.documento._ruta_artefacto, verbose_name='respuesta DIAN (cruda)'),
        ),
        migrations.AddField(
            model_name='documento',
            name='xml_archivo',
            field=models.FileField(blank=True, storage=apps.utilidades.almacenamiento.almacenamiento_backblaze, upload_to=apps.documentos.models.documento._ruta_artefacto, verbose_name='XML firmado'),
        ),
        migrations.RunPython(migrar_artefactos_a_storage, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='documento',
            name='respuesta_dian',
        ),
        migrations.RemoveField(
            model_name='documento',
            name='xml_firmado',
        ),
    ]
