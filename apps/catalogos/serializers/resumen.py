"""Serializer del índice de catálogos (`GET /api/catalogos/`)."""
from rest_framework import serializers

# Cómo se manda un valor de catálogo: por el `id` de la fila o por su código.
VALORES = ["id", "codigo"]


class AnexoSerializer(serializers.Serializer):
    documento = serializers.CharField(help_text="Documento electrónico del anexo.")
    version = serializers.CharField(help_text="Versión del anexo técnico, p. ej. `1.9`.")
    resolucion = serializers.CharField(help_text="Resolución DIAN que lo adopta.")


class UsoSerializer(serializers.Serializer):
    ruta = serializers.CharField(help_text="Ruta del recurso que recibe el campo.")
    campo = serializers.CharField(
        help_text="Ruta del campo en el cuerpo; `[]` marca una lista."
    )
    valor = serializers.ChoiceField(
        choices=VALORES,
        help_text="Qué se manda: el `id` de la fila o su `codigo` DIAN.",
    )


class CatalogoResumenSerializer(serializers.Serializer):
    nombre = serializers.CharField(help_text="El de la ruta: `/api/catalogos/<nombre>/`.")
    titulo = serializers.CharField()
    descripcion = serializers.CharField()
    lista_dian = serializers.CharField(
        help_text="Nombre de la lista de valores en el anexo técnico."
    )
    anexos_tecnicos = AnexoSerializer(many=True)
    registros = serializers.IntegerField()
    ultima_modificacion = serializers.DateTimeField(
        allow_null=True,
        help_text=(
            "La fila modificada más recientemente. Cada carga de catálogos "
            "vuelve a guardar las filas, así que marca la última carga."
        ),
    )
    url = serializers.URLField()
    url_exportar = serializers.URLField(help_text="El catálogo entero, sin paginar.")
    usos = UsoSerializer(many=True)
