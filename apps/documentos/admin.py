"""Registro de documentos en el admin de Django."""
from django.contrib import admin

from . import models


@admin.register(models.Adquirente)
class AdquirenteAdmin(admin.ModelAdmin):
    list_display = ("numero_identificacion", "razon_social", "municipio")
    search_fields = ("numero_identificacion", "razon_social")
    autocomplete_fields = ("municipio", "departamento", "pais")
    filter_horizontal = ("responsabilidades",)


class ImpuestoLineaInline(admin.TabularInline):
    model = models.ImpuestoLinea
    extra = 0
    autocomplete_fields = ("tributo",)


class LineaDocumentoInline(admin.StackedInline):
    model = models.LineaDocumento
    extra = 0
    autocomplete_fields = ("unidad_medida",)


@admin.register(models.DocumentoElectronico)
class DocumentoElectronicoAdmin(admin.ModelAdmin):
    list_display = (
        "numero", "tipo", "estado", "emisor", "adquirente",
        "fecha_emision", "total_a_pagar",
    )
    list_filter = ("tipo", "estado", "fecha_emision")
    search_fields = ("numero", "cufe_cude", "emisor__razon_social")
    autocomplete_fields = ("emisor", "adquirente", "resolucion")
    readonly_fields = ("cufe_cude", "xml_firmado", "respuesta_dian")
    inlines = [LineaDocumentoInline]


@admin.register(models.LineaDocumento)
class LineaDocumentoAdmin(admin.ModelAdmin):
    list_display = ("documento", "numero_linea", "descripcion", "cantidad", "valor_total")
    search_fields = ("descripcion", "codigo_producto")
    autocomplete_fields = ("documento", "unidad_medida")
    inlines = [ImpuestoLineaInline]
