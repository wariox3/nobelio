"""Fecha de pago: el XML la exige (``FechasPagos/FechaPago``) y faltaba."""
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nomina", "0001_initial"),
    ]

    operations = [
        # Obligatoria y sin valor por defecto en el modelo: el default de aquí
        # es solo para las filas existentes (no hay ninguna) y no se conserva.
        migrations.AddField(
            model_name="nomina",
            name="fecha_pago",
            field=models.DateField(
                default=django.utils.timezone.now, verbose_name="fecha de pago",
            ),
            preserve_default=False,
        ),
    ]
