"""Crea la tabla que respalda la caché de producción.

La caché la usa solo el throttling, y ahí el backend decide si los topes valen
algo: con el de por-proceso, cada worker de Gunicorn lleva su propia cuenta y un
tope de 3/hora se convierte en 3·N/hora, además de reiniciarse en cada
despliegue. En producción tiene que ser compartida.

Se eligió PostgreSQL, que ya está, en vez de pedir un Redis solo para esto: son
unos pocos contadores con vencimiento, no una caché caliente. `CACHE_URL` deja
apuntar a Redis el día que el volumen lo pida.

La tabla se crea aquí y no con `manage.py createcachetable` para que no haya un
paso manual que se pueda olvidar en un despliegue: el nombre tiene que coincidir
con el de `CACHES` en `config/settings/prod.py`.
"""
from django.core.management import call_command
from django.db import migrations

TABLA = "cache_general"


def crear(apps, schema_editor):
    call_command("createcachetable", TABLA, database=schema_editor.connection.alias)


def borrar(apps, schema_editor):
    schema_editor.execute(f'DROP TABLE IF EXISTS "{TABLA}"')


class Migration(migrations.Migration):

    initial = True
    dependencies = []
    operations = [migrations.RunPython(crear, borrar)]
