"""Formas del esquema OpenAPI que comparten documentos y nómina.

Las acciones que hablan con la DIAN —emitir y las consultas— devuelven lo mismo
en las dos apps, salvo el identificador (CUFE/CUDE o CUNE). Viven aquí para que
el componente sea uno solo: dos `inline_serializer` con el mismo nombre y
distinta definición chocarían en el esquema, y con nombres distintos el
integrador tendría dos tipos para la misma respuesta.
"""
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers


def campos_respuesta_dian():
    """Lo que devuelve la DIAN, común a emitir y a las consultas."""
    return {
        "estado": serializers.CharField(
            help_text="Estado en el sistema tras la operación.",
        ),
        "es_valido": serializers.BooleanField(
            help_text="`true` si la DIAN lo dio por válido.",
        ),
        "codigo_estado": serializers.CharField(
            help_text="Código de estado de la DIAN (`00` aceptado, `99` con errores…).",
        ),
        "descripcion": serializers.CharField(
            help_text="Descripción del estado según la DIAN.",
        ),
        "errores": serializers.ListField(
            child=serializers.CharField(),
            help_text="Reglas de rechazo y notificaciones, tal como las devuelve la DIAN.",
        ),
    }


def campo_track_id():
    return serializers.CharField(
        help_text=(
            "ZipKey si salió al Set de Pruebas; identificador del envío si salió "
            "síncrono."
        ),
    )


RESPUESTA_CONSULTA_DIAN = inline_serializer(
    name="ConsultaDianRespuesta", fields=campos_respuesta_dian(),
)
