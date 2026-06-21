"""API de catálogos DIAN (solo lectura)."""
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from . import models, serializers


class _CatalogoViewSet(viewsets.ReadOnlyModelViewSet):
    """Base de solo lectura para catálogos simples (código + nombre)."""

    serializer_class = serializers.ElementoCatalogoSerializer
    permission_classes = [AllowAny]
    search_fields = ["codigo", "nombre"]


class TipoFacturaViewSet(_CatalogoViewSet):
    queryset = models.TipoFactura.objects.all()


class TipoIdentificacionViewSet(_CatalogoViewSet):
    queryset = models.TipoIdentificacion.objects.all()


class TipoOrganizacionViewSet(_CatalogoViewSet):
    queryset = models.TipoOrganizacion.objects.all()


class ResponsabilidadFiscalViewSet(_CatalogoViewSet):
    queryset = models.ResponsabilidadFiscal.objects.all()


class TributoViewSet(_CatalogoViewSet):
    queryset = models.Tributo.objects.all()


class UnidadMedidaViewSet(_CatalogoViewSet):
    queryset = models.UnidadMedida.objects.all()


class FormaPagoViewSet(_CatalogoViewSet):
    queryset = models.FormaPago.objects.all()


class MedioPagoViewSet(_CatalogoViewSet):
    queryset = models.MedioPago.objects.all()


class MonedaViewSet(_CatalogoViewSet):
    queryset = models.Moneda.objects.all()


class PaisViewSet(_CatalogoViewSet):
    queryset = models.Pais.objects.all()


class DepartamentoViewSet(_CatalogoViewSet):
    queryset = models.Departamento.objects.all()


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Municipio.objects.select_related("departamento")
    serializer_class = serializers.MunicipioSerializer
    permission_classes = [AllowAny]
    search_fields = ["codigo", "nombre"]
