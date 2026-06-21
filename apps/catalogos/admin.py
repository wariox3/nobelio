"""Registro de catálogos en el admin de Django."""
from django.contrib import admin

from . import models

CATALOGOS_SIMPLES = [
    models.TipoFactura,
    models.TipoIdentificacion,
    models.TipoOrganizacion,
    models.ResponsabilidadFiscal,
    models.Tributo,
    models.UnidadMedida,
    models.FormaPago,
    models.MedioPago,
    models.Moneda,
    models.Pais,
    models.Departamento,
    models.ConceptoNotaCredito,
    models.ConceptoNotaDebito,
]


@admin.register(*CATALOGOS_SIMPLES)
class CatalogoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")
    search_fields = ("codigo", "nombre")
    list_filter = ("activo",)


@admin.register(models.Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "departamento", "activo")
    search_fields = ("codigo", "nombre")
    list_filter = ("activo",)
    autocomplete_fields = ("departamento",)
