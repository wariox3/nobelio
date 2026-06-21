"""
Carga las listas de valores DIAN (.gc) en la base de datos.

Recorre los archivos Genericode del repositorio y rellena los modelos de
catálogo. Es idempotente: usa ``update_or_create`` por código, así que puede
ejecutarse varias veces sin duplicar.

    python manage.py cargar_catalogos
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalogos import genericode as gc
from apps.catalogos import models

# Mapeo: nombre del archivo .gc (stem o prefijo) -> modelo destino.
MAPEO = {
    "TipoDocumento": models.TipoFactura,
    "TipoIdFiscal": models.TipoIdentificacion,
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


class Command(BaseCommand):
    help = "Carga las listas de valores DIAN (.gc) en la base de datos."

    @transaction.atomic
    def handle(self, *args, **opciones):
        for nombre, Modelo in MAPEO.items():
            try:
                lista = gc.cargar(nombre)
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

            self.stdout.write(
                f"  {Modelo._meta.verbose_name_plural:<28} "
                f"{creados:>4} creados, {actualizados:>4} actualizados"
            )

        self._enlazar_municipios()
        self.stdout.write(self.style.SUCCESS("Catálogos cargados."))

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
