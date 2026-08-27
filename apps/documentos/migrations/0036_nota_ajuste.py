from django.db import migrations, models

TIPO = ("nota_ajuste", "Nota de ajuste al documento soporte", "95")


def poblar_nota_ajuste(apps, schema_editor):
    """Siembra el tipo 95, como hizo la 0013 con los cuatro primeros."""
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    codigo, nombre, codigo_dian = TIPO
    DocumentoTipo.objects.update_or_create(
        codigo=codigo,
        defaults={"nombre": nombre, "codigo_dian": codigo_dian},
    )


def quitar_nota_ajuste(apps, schema_editor):
    """Solo si no hay documentos emitidos con ese tipo (la FK es PROTECT)."""
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    DocumentoTipo.objects.filter(codigo=TIPO[0], documentos__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0035_documento_notificado"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentotipo",
            name="codigo",
            field=models.CharField(
                choices=[
                    ("factura_venta", "Factura de venta"),
                    ("nota_credito", "Nota crédito"),
                    ("nota_debito", "Nota débito"),
                    ("documento_soporte", "Documento soporte"),
                    ("nota_ajuste", "Nota de ajuste al documento soporte"),
                    ("nomina", "Nómina electrónica"),
                ],
                help_text="Discriminador interno que define la lógica de generación.",
                max_length=30,
                unique=True,
                verbose_name="código",
            ),
        ),
        migrations.AlterField(
            model_name="documento",
            name="concepto_correccion",
            field=models.CharField(
                blank=True,
                help_text="ResponseCode del DiscrepancyResponse: por qué se "
                "corrige el documento referenciado. Los códigos válidos dependen "
                "del tipo de nota (ConceptoNotaCredito, ConceptoNotaDebito, "
                "ConceptoNotaAjuste). Vacío en lo que no es nota.",
                max_length=2,
                verbose_name="concepto de corrección",
            ),
        ),
        migrations.RunPython(poblar_nota_ajuste, quitar_nota_ajuste),
    ]
