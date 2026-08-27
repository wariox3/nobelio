"""Las condiciones del trabajador pasan a la nómina.

El maestro (``Empleado``) se queda con la identidad y con lo vigente; la nómina
guarda lo que se emitió, que es lo que cambia con el tiempo: sueldo, contrato,
tipo de cotizante, lugar de trabajo, datos de pago y código del trabajador.

Los ``default`` de este archivo son de un solo uso, para las filas que hubiera
al aplicar la migración: no hay ninguna, y no se conservan en el modelo.
"""
import django.db.models.deletion
from django.db import migrations, models


def _fk(a, related_name, verbose_name):
    return models.ForeignKey(
        default=1,
        on_delete=django.db.models.deletion.PROTECT,
        related_name=related_name,
        to=a,
        verbose_name=verbose_name,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("catalogos", "0006_datos_nomina"),
        ("nomina", "0004_nota_ajuste"),
    ]

    operations = [
        migrations.AddField(
            model_name="nomina",
            name="codigo_trabajador",
            field=models.CharField(
                blank=True, max_length=20,
                help_text="Va en NumeroSecuenciaXML, que identifica el documento.",
                verbose_name="código del trabajador",
            ),
        ),
        migrations.AddField(
            model_name="nomina",
            name="alto_riesgo_pension",
            field=models.BooleanField(
                default=False, verbose_name="alto riesgo de pensión",
            ),
        ),
        migrations.AddField(
            model_name="nomina",
            name="salario_integral",
            field=models.BooleanField(default=False, verbose_name="salario integral"),
        ),
        migrations.AddField(
            model_name="nomina",
            name="sueldo",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=15, verbose_name="sueldo",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="fecha_retiro",
            field=models.DateField(
                blank=True, null=True, verbose_name="fecha de retiro",
            ),
        ),
        migrations.AddField(
            model_name="nomina",
            name="lugar_trabajo_direccion",
            field=models.CharField(
                default="", max_length=255,
                verbose_name="dirección del lugar de trabajo",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="banco",
            field=models.CharField(blank=True, max_length=100, verbose_name="banco"),
        ),
        migrations.AddField(
            model_name="nomina",
            name="tipo_cuenta",
            field=models.CharField(
                blank=True, max_length=10,
                choices=[("ahorros", "Ahorros"), ("corriente", "Corriente")],
                verbose_name="tipo de cuenta",
            ),
        ),
        migrations.AddField(
            model_name="nomina",
            name="numero_cuenta",
            field=models.CharField(
                blank=True, max_length=30, verbose_name="número de cuenta",
            ),
        ),
        migrations.AddField(
            model_name="nomina",
            name="tipo_trabajador",
            field=_fk("catalogos.tipotrabajador", "nominas", "tipo de trabajador"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="subtipo_trabajador",
            field=_fk("catalogos.subtipotrabajador", "nominas", "subtipo de trabajador"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="tipo_contrato",
            field=_fk("catalogos.tipocontrato", "nominas", "tipo de contrato"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="lugar_trabajo_pais",
            field=_fk("catalogos.pais", "nominas", "país del lugar de trabajo"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="lugar_trabajo_departamento",
            field=_fk(
                "catalogos.departamento", "nominas",
                "departamento del lugar de trabajo",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="lugar_trabajo_municipio",
            field=_fk(
                "catalogos.municipio", "nominas", "municipio del lugar de trabajo",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="forma_pago",
            field=_fk("catalogos.formapago", "nominas", "forma de pago"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="nomina",
            name="medio_pago",
            field=_fk("catalogos.mediopago", "nominas", "medio de pago"),
            preserve_default=False,
        ),
    ]
