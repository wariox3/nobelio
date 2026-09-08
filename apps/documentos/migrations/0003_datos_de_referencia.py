"""Siembra los tipos y estados de documento.

No son datos de usuario: son el vocabulario con el que funciona el sistema. Un
documento no se puede crear sin un tipo y sin un estado, así que una base recién
creada sin estas filas no sirve para nada, y por eso van en una migración y no
en un comando que haya que acordarse de correr.

Vienen de siete migraciones anteriores al aplanado, que las fueron añadiendo a
medida que el proyecto cubría más documentos. Aquí están todas juntas, tal y
como quedaron.

``update_or_create`` para que sea idempotente y para no pisar un registro que ya
esté en uso.
"""
from django.db import migrations

TIPOS = [
    ("factura_venta", "Factura de venta", "01"),
    ("nota_credito", "Nota crédito", "91"),
    ("nota_debito", "Nota débito", "92"),
    ("documento_soporte", "Documento soporte", "05"),
    ("nomina", "Nómina electrónica", ""),
    ("nota_ajuste", "Nota de ajuste al documento soporte", "95"),
    ("documento_equivalente_pos", "Documento equivalente P.O.S.", "20"),
    ("nota_ajuste_de_credito", "Nota de ajuste crédito al documento equivalente", "94"),
    ("nota_ajuste_de_debito", "Nota de ajuste débito al documento equivalente", ""),
]

# `nombre` es el identificador que usa la lógica; `codigo` queda reservado para
# el código de la DIAN, que hoy no existe para el ciclo de vida interno.
ESTADOS = [
    ("borrador", "Borrador"),
    ("generado", "XML generado"),
    ("firmado", "Firmado"),
    ("enviado", "Enviado a la DIAN"),
    ("aceptado", "Aceptado por la DIAN"),
    ("rechazado", "Rechazado por la DIAN"),
]


def sembrar(apps, schema_editor):
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    for codigo, nombre, codigo_dian in TIPOS:
        DocumentoTipo.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "codigo_dian": codigo_dian},
        )

    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    for nombre, descripcion in ESTADOS:
        DocumentoEstado.objects.update_or_create(
            nombre=nombre, defaults={"descripcion": descripcion, "codigo": ""},
        )


def borrar(apps, schema_editor):
    """Solo lo que no esté en uso: el vocabulario se va si nadie lo referencia."""
    DocumentoTipo = apps.get_model("documentos", "DocumentoTipo")
    DocumentoTipo.objects.filter(
        codigo__in=[t[0] for t in TIPOS], documentos__isnull=True
    ).delete()
    DocumentoEstado = apps.get_model("documentos", "DocumentoEstado")
    DocumentoEstado.objects.filter(
        nombre__in=[e[0] for e in ESTADOS], documentos__isnull=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("documentos", "0002_initial")]
    operations = [migrations.RunPython(sembrar, borrar)]
