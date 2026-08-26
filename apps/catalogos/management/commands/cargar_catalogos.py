"""
Carga las listas de valores DIAN (.gc) en la base de datos.

Recorre los archivos Genericode del repositorio y rellena los modelos de
catálogo. Es idempotente: usa ``update_or_create`` por código, así que puede
ejecutarse varias veces sin duplicar.

    python manage.py cargar_catalogos
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalogos import genericode as gc
from apps.catalogos import models

# Mapeo: nombre del archivo .gc (stem o prefijo) -> modelo destino.
MAPEO = {
    "TipoDocumento": models.TipoFactura,
    "TipoIdentificacion": models.TipoIdentificacion,
    "TipoOrganizacion": models.TipoOrganizacion,
    "TipoResponsabilidad": models.ResponsabilidadFiscal,
    "TipoImpuesto": models.Tributo,
    "UnidadesMedida": models.UnidadMedida,
    "FormasPago": models.FormaPago,
    "MediosPago": models.MedioPago,
    "TipoMoneda": models.Moneda,
    "Paises": models.Pais,
    "Departamentos": models.Departamento,
    "Municipio": models.Municipio,
    "ConceptoNotaCredito": models.ConceptoNotaCredito,
    "ConceptoNotaDebito": models.ConceptoNotaDebito,
}

# Listas propias del documento soporte, en su subcarpeta (ver el README de
# `datos/listas/documento-soporte/`). Se cargan aparte y no se mezclan con las
# de factura: la caja de herramientas del DS es de 2022 y la de factura de 2026,
# así que solo se trae lo que de verdad falta.
#
# De momento solo el tipo de documento: su lista trae los códigos 05 y 95, que
# la de factura no tiene y que se suman a los suyos en la misma tabla. El resto
# (ConceptoNotaAjuste, TipoOperacion, TipoIdFiscal…) espera a que existan los
# modelos que las consuman.
SUBDIRECTORIO_SOPORTE = "documento-soporte"

MAPEO_SOPORTE = {
    "TipoDocumento": models.TipoFactura,
}


class Command(BaseCommand):
    help = "Carga las listas de valores DIAN (.gc) en la base de datos."

    @transaction.atomic
    def handle(self, *args, **opciones):
        self._cargar(MAPEO)
        # Después de las de factura, para que un código que estuviera en ambas
        # quede con el nombre de la lista más reciente, que es la de factura.
        self._cargar(MAPEO_SOPORTE, subdirectorio=SUBDIRECTORIO_SOPORTE)
        self._enlazar_municipios()
        self.stdout.write(self.style.SUCCESS("Catálogos cargados."))

    def _cargar(self, mapeo, subdirectorio=None):
        """Vuelca cada lista del mapeo en su modelo."""
        directorio = None
        if subdirectorio is not None:
            directorio = Path(settings.CATALOGOS_LISTAS_DIR) / subdirectorio

        for nombre, Modelo in mapeo.items():
            try:
                lista = gc.cargar(nombre, directorio)
            except FileNotFoundError:
                self.stdout.write(self.style.WARNING(f"  ⚠ no encontrada: {nombre}"))
                continue

            creados = actualizados = 0
            for fila in lista.filas:
                codigo = (fila.get("code") or "").strip()
                nombre_valor = (fila.get("name") or "").strip()
                if not codigo:
                    continue
                _, creado = Modelo.objects.update_or_create(
                    codigo=codigo,
                    defaults={"nombre": nombre_valor or codigo},
                )
                creados += creado
                actualizados += not creado

            etiqueta = Modelo._meta.verbose_name_plural
            if subdirectorio:
                etiqueta = f"{etiqueta} ({subdirectorio})"
            self.stdout.write(
                f"  {etiqueta:<28} "
                f"{creados:>4} creados, {actualizados:>4} actualizados"
            )

    def _enlazar_municipios(self):
        """Asocia cada municipio a su departamento por los 2 primeros dígitos."""
        departamentos = {d.codigo: d for d in models.Departamento.objects.all()}
        enlazados = 0
        municipios = models.Municipio.objects.filter(departamento__isnull=True)
        for municipio in municipios:
            depto = departamentos.get(municipio.codigo[:2])
            if depto:
                municipio.departamento = depto
                municipio.save(update_fields=["departamento"])
                enlazados += 1
        self.stdout.write(f"  municipios enlazados a departamento: {enlazados}")
