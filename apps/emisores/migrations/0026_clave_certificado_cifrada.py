"""La clave del .p12 pasa a guardarse cifrada.

Primero se ensancha la columna (el token Fernet abulta más que la clave) y
después se cifra lo que ya hubiera guardado en claro.
"""
import apps.utilidades.cifrado
from cryptography.fernet import InvalidToken
from django.db import migrations

TABLA = "emi_certificado"


def cifrar_claves(apps_registro, schema_editor):
    """Cifra las claves que estén en claro.

    Va por SQL y no por el modelo a propósito: el modelo histórico ya tiene el
    campo cifrado, así que leer y guardar con él haría el trabajo dos veces y
    escondería lo que realmente pasa aquí.

    Es idempotente. Lo que ya sea un token se deja como está, de modo que
    volver a pasar la migración —o pasarla sobre una base a medio migrar— no
    cifra nada dos veces.
    """
    cifrador = apps.utilidades.cifrado.cifrador()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, clave FROM {TABLA} WHERE clave <> ''")
        filas = cursor.fetchall()
        for id_, clave in filas:
            try:
                cifrador.decrypt(clave.encode())
            except InvalidToken:
                pass  # está en claro: hay que cifrarla
            else:
                continue  # ya era un token
            cursor.execute(
                f"UPDATE {TABLA} SET clave = %s WHERE id = %s",
                [apps.utilidades.cifrado.cifrar(clave), id_],
            )


def descifrar_claves(apps_registro, schema_editor):
    """Deshace el cifrado, para que la migración se pueda revertir.

    Deja las claves en claro otra vez: es lo que había antes, y sin esto la
    marcha atrás dejaría la columna ilegible. Solo tiene sentido acompañada de
    la reversión del `AlterField`, que es justo lo que hace `migrate 0025`.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, clave FROM {TABLA} WHERE clave <> ''")
        filas = cursor.fetchall()
        for id_, clave in filas:
            en_claro = apps.utilidades.cifrado.descifrar(clave)
            if en_claro != clave:
                cursor.execute(
                    f"UPDATE {TABLA} SET clave = %s WHERE id = %s",
                    [en_claro, id_],
                )


class Migration(migrations.Migration):

    dependencies = [
        ('emisores', '0025_fabricante_como_excepcion'),
    ]

    operations = [
        migrations.AlterField(
            model_name='certificado',
            name='clave',
            field=apps.utilidades.cifrado.ClaveCifradaField(max_length=512, verbose_name='clave del certificado'),
        ),
        migrations.RunPython(cifrar_claves, descifrar_claves),
    ]
