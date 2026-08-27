"""API de empleados."""
from rest_framework import filters, viewsets

from apps.nomina import serializers
from apps.nomina.models import Empleado
from apps.seguridad.alcance import AlcanceEmisorMixin


class EmpleadoViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """CRUD de trabajadores. Se crean una vez y cada nómina los referencia."""

    queryset = Empleado.objects.select_related(
        "emisor", "tipo_identificacion", "tipo_trabajador", "subtipo_trabajador",
        "tipo_contrato", "pais", "departamento", "municipio",
        "forma_pago", "medio_pago",
    )
    serializer_class = serializers.EmpleadoSerializer

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "numero_documento", "primer_nombre", "otros_nombres",
        "primer_apellido", "segundo_apellido", "codigo_trabajador",
    ]
    ordering_fields = [
        "primer_apellido", "primer_nombre", "numero_documento",
        "fecha_ingreso", "creado_en",
    ]

    def get_queryset(self):
        """Filtra por ``emisor`` (id) y ``activo`` (true/false).

        El filtro por emisor acota dentro del alcance, nunca lo amplía: el mixin
        ya restringió el queryset antes de llegar aquí.
        """
        qs = super().get_queryset()
        params = self.request.query_params
        if emisor := params.get("emisor"):
            qs = qs.filter(emisor=emisor)
        if (activo := params.get("activo")) is not None:
            qs = qs.filter(activo=activo.lower() in ("1", "true", "si", "sí"))
        return qs
