"""Índice de catálogos: `GET /api/catalogos/`."""
from django.db.models import Count, Max
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogos import serializers
from apps.catalogos.registro import CATALOGOS


class CatalogosView(APIView):
    """Los catálogos disponibles, con lo que hace falta para publicarlos."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "catalogos"
    throttle_scope_rafaga = "catalogos_rafaga"

    @extend_schema(
        summary="Listar los catálogos",
        description=(
            "Todos los catálogos, sin paginar: qué son, de qué lista y anexo "
            "técnico DIAN salen, cuántos registros tienen, cuándo cambiaron por "
            "última vez y qué campos de la API reciben sus valores."
        ),
        responses=serializers.CatalogoResumenSerializer(many=True),
    )
    def get(self, request):
        return Response([self._resumen(request, c) for c in CATALOGOS])

    def _resumen(self, request, catalogo):
        cifras = catalogo.modelo.objects.aggregate(
            registros=Count("pk"), ultima_modificacion=Max("actualizado_en"),
        )
        # El nombre de la ruta es el que le da el router: el del modelo.
        ruta = reverse(f"{catalogo.modelo._meta.model_name}-list")
        url = request.build_absolute_uri(ruta)
        return serializers.CatalogoResumenSerializer({
            "nombre": catalogo.nombre,
            "titulo": catalogo.modelo._meta.verbose_name_plural.capitalize(),
            "descripcion": catalogo.descripcion,
            "lista_dian": catalogo.lista_dian,
            "anexos_tecnicos": catalogo.anexos,
            **cifras,
            "url": url,
            "url_exportar": f"{url}exportar/",
            "usos": catalogo.usos,
        }).data
