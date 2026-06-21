"""Registro de emisores en el admin de Django."""
from django.contrib import admin

from . import models


class SoftwareDianInline(admin.TabularInline):
    model = models.SoftwareDian
    extra = 0


class CertificadoDigitalInline(admin.TabularInline):
    model = models.CertificadoDigital
    extra = 0


class ResolucionFacturacionInline(admin.TabularInline):
    model = models.ResolucionFacturacion
    extra = 0


@admin.register(models.Emisor)
class EmisorAdmin(admin.ModelAdmin):
    list_display = ("numero_identificacion", "razon_social", "municipio", "activo")
    search_fields = ("numero_identificacion", "razon_social", "nombre_comercial")
    list_filter = ("activo", "tipo_organizacion")
    autocomplete_fields = ("municipio", "departamento", "pais")
    filter_horizontal = ("responsabilidades",)
    inlines = [SoftwareDianInline, CertificadoDigitalInline, ResolucionFacturacionInline]


@admin.register(models.ResolucionFacturacion)
class ResolucionFacturacionAdmin(admin.ModelAdmin):
    list_display = (
        "numero_resolucion", "emisor", "prefijo",
        "rango_desde", "rango_hasta", "vigente_hasta", "activa",
    )
    search_fields = ("numero_resolucion", "prefijo", "emisor__razon_social")
    list_filter = ("activa", "tipo_factura")
