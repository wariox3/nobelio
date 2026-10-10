"""Serializers de los eventos RADIAN de las facturas recibidas."""
from rest_framework import serializers

from apps.catalogos.memoria import RelacionDeCatalogo
from apps.catalogos.models import ConceptoReclamo, TipoIdentificacion
from apps.dian.ubl import evento as ubl_evento
from apps.recepcion.models import Evento


class EventoSerializer(serializers.ModelSerializer):
    """Un evento. Los XML se bajan por sus rutas (``xml/`` y ``respuesta/``)."""

    codigo = serializers.CharField(source="evento_radian.codigo", read_only=True)
    descripcion = serializers.CharField(source="evento_radian.nombre", read_only=True)
    concepto_reclamo = serializers.SlugRelatedField(slug_field="codigo", read_only=True)
    tiene_xml = serializers.SerializerMethodField()
    tiene_respuesta = serializers.SerializerMethodField()

    class Meta:
        model = Evento
        fields = [
            "id", "documento", "emisor", "codigo", "descripcion", "numero",
            "estado", "ambiente", "cude", "fecha", "hora",
            "persona_tipo_identificacion", "persona_numero_identificacion",
            "persona_nombres", "persona_apellidos", "persona_cargo", "persona_area",
            "concepto_reclamo", "respuesta_codigo", "respuesta_descripcion",
            "enviado_en", "intentos", "usuario", "tiene_xml", "tiene_respuesta",
            "creado_en",
        ]
        read_only_fields = fields

    def get_tiene_xml(self, evento) -> bool:
        return bool(evento.xml_archivo)

    def get_tiene_respuesta(self, evento) -> bool:
        return bool(evento.respuesta_archivo)


class PersonaQueRecibeSerializer(serializers.Serializer):
    """Quien recibió la factura o la mercancía (030 y 032)."""

    tipo_identificacion = RelacionDeCatalogo(
        queryset=TipoIdentificacion.objects.all(),
        help_text="Id del catálogo de tipos de identificación (13 = cédula).",
    )
    numero_identificacion = serializers.CharField(max_length=20)
    nombres = serializers.CharField(max_length=100)
    apellidos = serializers.CharField(max_length=100)
    cargo = serializers.CharField(max_length=100, required=False, default="")
    area = serializers.CharField(max_length=100, required=False, default="")


class SolicitudEventoSerializer(serializers.Serializer):
    """La entrada de ``POST /api/recepcion/documento/{id}/evento/``."""

    codigo = serializers.ChoiceField(
        choices=ubl_evento.EVENTOS,
        help_text="030 acuse, 031 reclamo, 032 recibo del bien o servicio, 033 aceptación.",
    )
    persona = PersonaQueRecibeSerializer(
        required=False,
        help_text="Solo 030 y 032. Sin ella, va la persona que recibe configurada en el emisor.",
    )
    concepto_reclamo = serializers.SlugRelatedField(
        slug_field="codigo", queryset=ConceptoReclamo.objects.filter(activo=True),
        required=False,
        help_text="Solo 031, y obligatorio ahí: el código de la lista de conceptos de reclamo.",
    )
