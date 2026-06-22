"""Base de solo lectura para los catálogos simples."""
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from apps.catalogos import serializers


class _CatalogoViewSet(viewsets.ReadOnlyModelViewSet):
    """Base de solo lectura para catálogos simples (código + nombre)."""

    serializer_class = serializers.ElementoCatalogoSerializer
    permission_classes = [AllowAny]
    search_fields = ["codigo", "nombre"]
