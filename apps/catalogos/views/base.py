"""Base de solo lectura para los catálogos.

Los catálogos son públicos: son las listas oficiales de la DIAN, no dependen de
ningún emisor y el sitio de documentación los descarga al compilar. Por eso sin
autenticación —como las demás rutas públicas, una credencial inválida no debe
convertir en 401 algo que no la necesita— y con su propio tope por IP.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.catalogos import serializers


class _CatalogoViewSet(viewsets.ReadOnlyModelViewSet):
    """Base de solo lectura para catálogos simples (código + nombre)."""

    serializer_class = serializers.ElementoCatalogoSerializer
    search_fields = ["codigo", "nombre"]
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "catalogos"
    throttle_scope_rafaga = "catalogos_rafaga"

    @extend_schema(filters=True)
    @action(detail=False, pagination_class=None)
    def exportar(self, request):
        """El catálogo entero, sin paginar y con los mismos campos que el detalle."""
        filas = self.filter_queryset(self.get_queryset())
        return Response(self.get_serializer(filas, many=True).data)
