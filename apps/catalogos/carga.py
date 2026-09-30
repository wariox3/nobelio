"""Carga de los catálogos desde sus listas `.gc`.

Todo catálogo sale de un `.gc` (`datos/listas/`) y cada código trae en la
columna `id` su id fijo: la tabla no tiene autoincremental. Cargar es un
*upsert* por id en una sola sentencia por lista: crea lo que falta y actualiza
nombre y columnas extra de lo que ya está, así que se puede repetir cuando se
quiera —al actualizar una lista, por ejemplo— sin duplicar nada.

Lo usa `manage.py cargar_catalogos`, y las pruebas para cargar solo los
catálogos que necesitan (``cargar([Moneda])``).
"""
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from apps.catalogos import genericode as gc
from apps.catalogos import models


@dataclass(frozen=True)
class Lista:
    """Un `.gc` y el modelo al que va."""

    archivo: str
    modelo: type
    # Subcarpeta de `datos/listas/`; vacía, la raíz.
    carpeta: str = ""
    # Columnas del `.gc` que van a un campo del mismo nombre, además de
    # `code` (codigo), `name` (nombre) e `id`.
    extras: tuple = ()

    @property
    def etiqueta(self):
        etiqueta = str(self.modelo._meta.verbose_name_plural)
        return f"{etiqueta} ({self.carpeta})" if self.carpeta else etiqueta

    def filas(self):
        """``{id: campos}`` de la lista; un código repetido queda una vez."""
        directorio = settings.CATALOGOS_LISTAS_DIR / self.carpeta
        filas = {}
        for fila in gc.cargar(self.archivo, directorio).filas:
            codigo = (fila.get("code") or "").strip()
            if not codigo:
                continue
            id_fijo = (fila.get("id") or "").strip()
            if not id_fijo.isdigit():
                raise ValueError(
                    f"{self.archivo}: el código {codigo} no tiene `id` en el .gc."
                )
            campos = {
                "codigo": codigo,
                "nombre": (fila.get("name") or "").strip() or codigo,
            }
            for columna in self.extras:
                campos[columna] = (fila.get(columna) or "").strip()
            filas[int(id_fijo)] = campos
        return filas


# En orden de carga: los departamentos antes que los municipios, que se
# enlazan a ellos.
LISTAS = (
    # Caja de herramientas de factura electrónica. A `TipoDocumento` y
    # `TipoIdentificacion` se les añadieron a mano el `20` (P.O.S.) y el `47`
    # (PEP), que no vienen en ninguna lista de la DIAN.
    Lista("TipoDocumento", models.TipoFactura),
    Lista("TipoIdentificacion", models.TipoIdentificacion),
    Lista("TipoOrganizacion", models.TipoOrganizacion),
    Lista("TipoResponsabilidad", models.ResponsabilidadFiscal),
    Lista("TipoImpuesto", models.Tributo),
    Lista("UnidadesMedida", models.UnidadMedida),
    Lista("FormasPago", models.FormaPago),
    Lista("MediosPago", models.MedioPago),
    Lista("TipoMoneda", models.Moneda),
    Lista("Paises", models.Pais),
    Lista("Departamentos", models.Departamento),
    # `codigo_postal` tampoco es de la DIAN: sale de 4-72 (README de
    # `datos/listas/`).
    Lista("Municipio", models.Municipio, extras=("codigo_postal",)),
    Lista("ConceptoNotaCredito", models.ConceptoNotaCredito),
    Lista("ConceptoNotaDebito", models.ConceptoNotaDebito),
    # Documento soporte: solo lo que la de factura no trae, el 05 y el 95.
    Lista("TipoDocumento", models.TipoFactura, carpeta="documento-soporte"),
    # Nómina: la DIAN solo las publica en el PDF del anexo; transcritas a `.gc`.
    Lista("PeriodoNomina", models.PeriodoNomina, carpeta="nomina"),
    Lista("TipoContrato", models.TipoContrato, carpeta="nomina"),
    Lista("TipoTrabajador", models.TipoTrabajador, carpeta="nomina"),
    Lista("SubTipoTrabajador", models.SubTipoTrabajador, carpeta="nomina"),
)


@transaction.atomic
def cargar(modelos=None):
    """Carga las listas de ``modelos`` (todas si no se dicen).

    Devuelve ``[(lista, creados, actualizados)]``.
    """
    resultado = []
    for lista in LISTAS:
        if modelos is not None and lista.modelo not in modelos:
            continue
        resultado.append((lista, *_volcar(lista)))
    return resultado


def _volcar(lista):
    Modelo = lista.modelo
    filas = lista.filas()
    if not filas:
        return 0, 0

    if Modelo is models.Municipio:
        # Los dos primeros dígitos del código DANE son el departamento.
        departamentos = dict(models.Departamento.objects.values_list("codigo", "id"))
        for campos in filas.values():
            campos["departamento_id"] = departamentos.get(campos["codigo"][:2])

    existentes = set(
        Modelo.objects.filter(id__in=filas).values_list("id", flat=True)
    )
    campos_a_actualizar = [
        *next(iter(filas.values()), {}).keys(), "actualizado_en",
    ]
    # Por id: un código que ya estuviera con otro id choca con la unicidad de
    # `codigo` en vez de quedar duplicado.
    Modelo.objects.bulk_create(
        [Modelo(id=id_fijo, **campos) for id_fijo, campos in filas.items()],
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=campos_a_actualizar,
    )
    return len(filas) - len(existentes), len(existentes)
