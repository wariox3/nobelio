import json
import re

import django.db.models.deletion
from django.db import migrations, models

_RE_ERROR = re.compile(
    r"Regla:\s*(?P<regla>[^,]+),\s*(?P<tipo>[^:]+):\s*(?P<mensaje>.*)",
    re.IGNORECASE | re.DOTALL,
)


def _parsear(texto):
    m = _RE_ERROR.match(texto.strip())
    if not m:
        return {"regla": "", "tipo": "otro", "mensaje": texto.strip()}
    et = m.group("tipo").strip().lower()
    tipo = "rechazo" if et.startswith("rechaz") else "notificacion" if et.startswith("notif") else "otro"
    return {"regla": m.group("regla").strip(), "tipo": tipo, "mensaje": m.group("mensaje").strip()}


def migrar_errores_a_filas(apps, schema_editor):
    # Se lee la columna JSON por SQL directo: el related_name="errores" del nuevo
    # FK coexiste con el JSONField "errores" en esta migración y choca por nombre.
    DocumentoError = apps.get_model("documentos", "DocumentoError")
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT id, errores FROM doc_documento")
        filas_doc = cur.fetchall()

    filas = []
    for doc_id, errores in filas_doc:
        # Según el driver, el JSONField puede llegar como texto JSON o ya como lista.
        if isinstance(errores, str):
            errores = json.loads(errores or "[]")
        for e in (errores or []):
            datos = _parsear(e)
            if datos["tipo"] == "notificacion":  # solo se guardan rechazos
                continue
            filas.append(DocumentoError(documento_id=doc_id, **datos))
    DocumentoError.objects.bulk_create(filas)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0015_remove_documento_respuesta_dian_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentoError',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creado_en', models.DateTimeField(auto_now_add=True, verbose_name='creado en')),
                ('actualizado_en', models.DateTimeField(auto_now=True, verbose_name='actualizado en')),
                ('regla', models.CharField(blank=True, max_length=20, verbose_name='regla')),
                ('tipo', models.CharField(choices=[('rechazo', 'Rechazo'), ('notificacion', 'Notificación'), ('otro', 'Otro')], default='otro', max_length=20, verbose_name='tipo')),
                ('mensaje', models.TextField(verbose_name='mensaje')),
                ('documento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='errores', to='documentos.documento', verbose_name='documento')),
            ],
            options={
                'verbose_name': 'error de documento',
                'verbose_name_plural': 'errores de documento',
                'db_table': 'doc_documento_error',
                'ordering': ['id'],
            },
        ),
        migrations.RunPython(migrar_errores_a_filas, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='documento',
            name='errores',
        ),
    ]
