"""Siembra los catálogos de nómina, que la DIAN no publica en Genericode.

Las cuatro listas del numeral 5.5 del anexo de nómina (Res. 000013/2021) solo
existen dentro del PDF: no hay `.gc` que `cargar_catalogos` pueda leer, así que
los datos van aquí. Se transcriben del anexo con los acentos corregidos.
"""
from django.db import migrations

PERIODOS_NOMINA = [
    ("1", "Semanal"),
    ("2", "Decenal"),
    ("3", "Catorcenal"),
    ("4", "Quincenal"),
    ("5", "Mensual"),
    ("6", "Otro"),
]

TIPOS_CONTRATO = [
    ("1", "Término fijo"),
    ("2", "Término indefinido"),
    ("3", "Obra o labor"),
    ("4", "Aprendizaje"),
    ("5", "Prácticas o pasantías"),
]

TIPOS_TRABAJADOR = [
    ("01", "Dependiente"),
    ("02", "Servicio doméstico"),
    ("04", "Madre comunitaria"),
    ("12", "Aprendices del SENA en etapa lectiva"),
    ("18", "Funcionarios públicos sin tope máximo de IBC"),
    ("19", "Aprendices del SENA en etapa productiva"),
    ("21", "Estudiantes de postgrado en salud"),
    ("22", "Profesor de establecimiento particular"),
    ("23", "Estudiantes aportes solo riesgos laborales"),
    ("30", "Dependiente entidades o universidades públicas con régimen "
           "especial en salud"),
    ("31", "Cooperados o pre cooperativas de trabajo asociado"),
    ("47", "Trabajador dependiente de entidad beneficiaria del sistema general "
           "de participaciones - aportes patronales"),
    ("51", "Trabajador de tiempo parcial"),
    ("54", "Pre pensionado de entidad en liquidación"),
    ("56", "Pre pensionado con aporte voluntario a salud"),
    ("58", "Estudiantes de prácticas laborales en el sector público"),
]

SUBTIPOS_TRABAJADOR = [
    ("00", "No aplica"),
    ("01", "Dependiente pensionado por vejez activo"),
]

# El único código del numeral 5.2.1 que le falta al catálogo de identificación:
# el `91` (NUIP) ya estaba —viene en la lista de factura— y el `47` solo
# aparecía en la del documento soporte, que no se carga.
TIPO_IDENTIFICACION_PEP = ("47", "PEP")


def poblar(apps, schema_editor):
    tablas = [
        ("PeriodoNomina", PERIODOS_NOMINA),
        ("TipoContrato", TIPOS_CONTRATO),
        ("TipoTrabajador", TIPOS_TRABAJADOR),
        ("SubTipoTrabajador", SUBTIPOS_TRABAJADOR),
        ("TipoIdentificacion", [TIPO_IDENTIFICACION_PEP]),
    ]
    for nombre_modelo, filas in tablas:
        Modelo = apps.get_model("catalogos", nombre_modelo)
        for codigo, nombre in filas:
            Modelo.objects.update_or_create(
                codigo=codigo, defaults={"nombre": nombre},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("catalogos", "0005_periodonomina_subtipotrabajador_tipocontrato_and_more"),
    ]

    operations = [
        # Sin reversa, como la siembra de tipos de documento (documentos.0013):
        # son filas de catálogo referenciadas con FK PROTECT, así que borrarlas
        # al revertir fallaría en cuanto haya un empleado que las use.
        migrations.RunPython(poblar, migrations.RunPython.noop),
    ]
