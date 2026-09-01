from django.db import migrations


class Migration(migrations.Migration):
    """Las resoluciones no son solo de facturación: también numeran el DS."""

    dependencies = [
        ('emisores', '0019_quitar_codigo_interface'),
        # `documentos.0001_initial` crea `Documento.resolucion` apuntando al
        # nombre viejo, y es este RenameModel el que reapunta esa referencia al
        # nuevo. Para poder hacerlo, el campo tiene que existir ya en el estado.
        #
        # Nunca se declaró y el grafo lo cumplía por casualidad: `documentos`
        # no dependía de ninguna migración de `emisores` posterior a la 0001,
        # así que el orden topológico dejaba a `documentos.0001` delante. En
        # cuanto una migración de `documentos` depende de una `emisores`
        # reciente —la del documento equivalente P.O.S.— el orden se invierte,
        # el rename corre antes de que el campo exista y `migrate` muere con
        # "lazy reference to 'emisores.resolucionfacturacion'".
        #
        # Solo fija el orden: no cambia ninguna operación ni toca la base.
        ('documentos', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='ResolucionFacturacion',
            new_name='Resolucion',
        ),
        migrations.AlterModelTable(
            name='resolucion',
            table='emi_resolucion',
        ),
        migrations.AlterModelOptions(
            name='resolucion',
            options={
                'ordering': ['-fecha_resolucion'],
                'verbose_name': 'resolución',
                'verbose_name_plural': 'resoluciones',
            },
        ),
    ]
