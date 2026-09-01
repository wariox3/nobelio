"""La caja de venta deja de ser un maestro y pasa a ser tres columnas del P.O.S.

Se creó como ``CajaVenta`` en la ``0040`` y se descartó antes de que llegara a
usarse: la DIAN no contrasta la caja contra ningún registro —solo quiere tres
cadenas en la extensión ``InformacionCajaVenta``— y un maestro obligaba al punto
de venta a conocer un id nuestro y a darse de alta antes de poder vender.

De paso arregla algo que el maestro traía de serie: con la FK, mover una caja de
pasillo reescribía la ubicación de todos los tiquetes anteriores. Como columnas
del documento, lo guardado es lo emitido — el mismo criterio que ``Nomina``
aplica a las condiciones del periodo.

Se puede borrar la tabla sin más porque nunca tuvo filas: se comprobó antes de
escribir esta migración (0 cajas y 0 ``DocumentoPOS``), y ninguna de las dos
llegó a estar en un despliegue.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0043_choices_notas_de"),
    ]

    operations = [
        migrations.RemoveField(model_name="documentopos", name="caja"),
        migrations.AddField(
            model_name="documentopos",
            name="caja_placa",
            field=models.CharField(
                default="", max_length=50,
                help_text="Placa de inventario de la caja (par `PlacaCaja`).",
                verbose_name="placa de la caja",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documentopos",
            name="caja_ubicacion",
            field=models.CharField(
                default="", max_length=255,
                help_text="Dónde estaba la caja (par `UbicaciónCaja`, con "
                          "tilde: es el literal que compara la DIAN).",
                verbose_name="ubicación de la caja",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documentopos",
            name="caja_tipo",
            field=models.CharField(
                default="", max_length=100,
                help_text="Par `TipoCaja`. El anexo no da lista de valores; la "
                          "ejemplificación oficial usa texto libre ('Caja de apoyo').",
                verbose_name="tipo de caja",
            ),
            preserve_default=False,
        ),
        migrations.RemoveConstraint(
            model_name="cajaventa", name="caja_placa_unica_por_emisor",
        ),
        migrations.DeleteModel(name="CajaVenta"),
    ]
