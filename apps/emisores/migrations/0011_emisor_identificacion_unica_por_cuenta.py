"""La identificación del emisor pasa a ser única por cuenta, no global.

El mismo NIT puede estar dado de alta en varias cuentas a la vez, y cada fila
lleva sus propios datos: una integración para facturación y otra para nómina
pueden registrar correos distintos, y durante una migración de proveedor el
cliente convive en las dos hasta que termina el traslado. Como el software,
el certificado y las resoluciones cuelgan del emisor, cada cuenta genera los
suyos y el histórico de la anterior queda intacto.

Lo que no puede repetirse entre esas filas es una resolución de numeración
**activa**; eso lo comprueba ``resolucion_activa_en_otra_cuenta`` en la capa de
aplicación, porque requiere mirar filas de otro emisor.

La marcha atrás solo es posible si no hay NITs repetidos entre cuentas.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("emisores", "0010_softwaredian_set_pruebas_aceptado"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="emisor",
            name="emisor_identificacion_unica",
        ),
        migrations.AddConstraint(
            model_name="emisor",
            constraint=models.UniqueConstraint(
                fields=("cuenta", "tipo_identificacion", "numero_identificacion"),
                name="emisor_identificacion_unica_por_cuenta",
            ),
        ),
    ]
