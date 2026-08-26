# Listas de valores DIAN — Documento Soporte (Genericode `.gc`)

Listas de la *Caja de herramientas Documento Soporte Validación Previa*,
carpeta `Version 1.1` (Resolución 000167 del 30/DIC/2021).

Van en subcarpeta y no junto a las de factura por dos razones: los nombres de
archivo chocan (`TipoDocumento-2.1.gc` existe en ambas cajas con contenidos
distintos) y la caja del DS es de **2022**, mientras que las listas de factura
del directorio padre vienen de la *Caja de herramientas FE V19 (v2026)*. Mezclarlas
sería degradar las de factura a una versión anterior.

`cargar_catalogos` **no lee esta carpeta**: `genericode.listar_archivos()` hace
`glob("*.gc")` sin recursión sobre el directorio configurado. Cuando se implemente
la emisión de documento soporte habrá que decidir explícitamente qué se carga.

Ver [docs/anexo-documento-soporte.md](../../../../../docs/anexo-documento-soporte.md)
para qué significa cada lista.

## Qué se copió

Solo lo que el directorio padre **no tiene** o tiene con otro contenido:

| Archivo | Motivo |
|---------|--------|
| `TipoDocumento-2.1.gc` | Complementaria, no una versión: solo trae `05` y `95`; la de factura trae `01`–`04`, `91`, `92` |
| `TipoOperacion-2.1.gc` | Nueva. `CustomizationID` del DS: 10 Residente / 11 No Residente |
| `ConceptoNotaAjuste-2.1.gc` | Nueva. `ResponseCode` de la nota de ajuste |
| `AlgoritmoCUDS-2.1.gc` | Nueva |
| `TipoIdFiscal-2.1.gc` | Nueva. Identificación del vendedor no residente |
| `TarifaImpuestoReteRenta-2.1.gc` | Nueva |
| `FormaGeneracionTransmision.gc` | Nueva |
| `TiposRespuesta.gc` | Nueva |
| `TipoResponsabilidad-2.1.gc` | Añade `O-48` y `O-49` (ver abajo) |

## Qué NO se copió

Las demás listas del zip son las **mismas de factura pero más viejas**, o
subconjuntos suyos. Se dejaron fuera para no pisar las de 2026:

- Idénticas al padre: `CodigoDescuento`, `Departamentos`, `EventoDocumento`,
  `FormasPago`, `LanguageCode`, `MediosPago`, `Municipio`, `TipoAmbiente`,
  `TipoMoneda`, `TipoOrganizacion`.
- Versión 2022 de una lista que el padre ya tiene más reciente: `Paises`,
  `UnidadesMedida`, `TarifaImpuestoIVA`, `TarifaImpuestoReteIVA`,
  `TipoCodigoProducto`, `CodigoPrecioReferencia`, `CodigoPostal`.
- Subconjunto: `TipoImpuesto` (el DS solo usa `01` IVA, `05` ReteIVA,
  `06` ReteRenta; los tres ya están en la lista del padre, que tiene 16).

## Pendiente de decidir: `TipoResponsabilidad`

La lista del DS trae **`O-48` (Impuesto sobre las ventas – IVA)** y **`O-49`
(No responsable de IVA)**, que la lista de factura del directorio padre **no
tiene** (solo `O-13`, `O-15`, `O-23`, `O-47`, `R-99-PN`).

No se fusionaron por cuenta propia: si esos dos códigos faltan también para
factura, es un hueco del catálogo de factura y hay que corregirlo allí a
conciencia, no de rebote al añadir el documento soporte. Se copia la lista del DS
tal cual para dejar la diferencia a la vista.

Otra diferencia menor: `R-99-PN` se llama "No aplica" en esta lista y
"No responsable" en la de factura. `apps/dian/ubl.py` usa el **código**, no el
nombre, así que no afecta.
