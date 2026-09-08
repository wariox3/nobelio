"""Limpia lo que dejó el renombrado de `email_verificado` a `is_verified`.

La 0009 se aplicó con el nombre viejo (`0009_usuario_email_verificado`) y
después se reescribió con el nuevo. Django identifica las migraciones por su
nombre, así que la nueva se aplicó como si fuera otra: creó `is_verified` y
dejó `email_verificado` en la tabla, `NOT NULL` y sin valor por defecto. Como el
modelo ya no tiene ese campo, ningún INSERT lo rellena y dar de alta un usuario
revienta con una violación de no-nulo.

Se arregla en dos partes, las dos idempotentes para que valga igual en una base
que pasó por eso y en una que no:

1. Quitar la columna huérfana.
2. Borrar el registro de la migración vieja, cuyo archivo ya no existe. No
   rompe nada mientras está —Django ignora lo aplicado que no está en disco—,
   pero deja el historial mintiendo sobre lo que se aplicó.

Sin vuelta atrás: recrear la columna solo devolvería el problema.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('seguridad', '0010_usuario_nombre_corto'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE seg_usuario DROP COLUMN IF EXISTS email_verificado;',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=(
                "DELETE FROM django_migrations "
                "WHERE app = 'seguridad' AND name = '0009_usuario_email_verificado';"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
