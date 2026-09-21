"""Renumera departamentos y municipios con los ids del catálogo de torio.

torio manda el departamento y el municipio del adquiriente con el id de su
propio catálogo, que llega aquí como llave primaria. Allí el id es la posición
del código DANE en orden (05 Antioquia = 1 … 99 Vichada = 33; 05001 Medellín =
1 …), mientras que aquí era el orden del `.gc`, alfabético por nombre en el
caso de los departamentos. Los municipios ya coincidían; se renumeran igual
para que la regla valga aunque una base se hubiera cargado en otro orden.

Los países no se tocan: los dos catálogos ya comparten id.

Las llaves foráneas se arrastran a mano porque la llave primaria cambia. En
PostgreSQL las restricciones de Django son `DEFERRABLE INITIALLY DEFERRED`, así
que dentro de la transacción de la migración el orden de los UPDATE da igual.
"""
from django.core.management.color import no_style
from django.db import migrations


def _renumerar(apps, schema_editor, nombre_modelo):
    Modelo = apps.get_model("catalogos", nombre_modelo)
    codigos = list(Modelo.objects.order_by("codigo").values_list("id", flat=True))
    mapa = {viejo: nuevo for nuevo, viejo in enumerate(codigos, start=1) if viejo != nuevo}
    if not mapa:
        return

    conexion = schema_editor.connection
    q = schema_editor.quote_name
    casos = " ".join(f"WHEN {viejo} THEN {nuevo}" for viejo, nuevo in mapa.items())
    viejos = ", ".join(str(viejo) for viejo in mapa)

    with conexion.cursor() as cursor:
        for relacion in Modelo._meta.related_objects:
            # La inversa de una ForeignKey es `one_to_many`, no `many_to_one`.
            if relacion.many_to_many:
                continue
            tabla = q(relacion.related_model._meta.db_table)
            columna = q(relacion.field.column)
            cursor.execute(
                f"UPDATE {tabla} SET {columna} = CASE {columna} {casos} END "
                f"WHERE {columna} IN ({viejos})"
            )

        # En dos pasos para no chocar con la unicidad de la llave primaria a
        # mitad de la permutación: primero a negativo, luego al definitivo.
        tabla = q(Modelo._meta.db_table)
        cursor.execute(
            f"UPDATE {tabla} SET id = -(CASE id {casos} END) WHERE id IN ({viejos})"
        )
        cursor.execute(f"UPDATE {tabla} SET id = -id WHERE id < 0")

        for sql in conexion.ops.sequence_reset_sql(no_style(), [Modelo]):
            cursor.execute(sql)


def renumerar(apps, schema_editor):
    _renumerar(apps, schema_editor, "Departamento")
    _renumerar(apps, schema_editor, "Municipio")


class Migration(migrations.Migration):

    dependencies = [
        ("catalogos", "0003_municipio_codigo_postal"),
        # Las tablas que apuntan a los catálogos tienen que existir para
        # arrastrar sus llaves.
        ("documentos", "0005_eventos"),
        ("emisores", "0006_webhook"),
        ("nomina", "0002_eventos"),
    ]

    operations = [
        migrations.RunPython(renumerar, migrations.RunPython.noop),
    ]
