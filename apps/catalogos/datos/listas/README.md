# Listas de valores (Genericode `.gc`)

Copia de las listas de valores oficiales de la DIAN incluidas en la
*Caja de herramientas FE V19 (v2026)*. Se versionan en el repo para que el
proyecto sea autocontenido y la carga de catálogos sea reproducible.

Se leen con `apps/catalogos/genericode.py`.

## Cómo se cargan los catálogos

**Todo catálogo sale de un `.gc`.** Ninguno se siembra por migración ni se crea
a mano: las migraciones solo crean las tablas. Qué lista va a qué modelo lo
dice un único registro, `LISTAS` en `apps/catalogos/carga.py`:

| Carpeta | Listas |
|---------|--------|
| esta | las 14 de factura: tipo de documento, identificación, organización, responsabilidad, tributo, unidad de medida, forma y medio de pago, moneda, país, departamento, municipio, conceptos de nota crédito y débito; y `Concepto de Reclamo` (evento RADIAN 031) |
| `documento-soporte/` | `TipoDocumento` (el `05` y el `95`), que se suman a los tipos de factura |
| `nomina/` | `PeriodoNomina`, `TipoContrato`, `TipoTrabajador`, `SubTipoTrabajador` |
| `radian/` | `EventoRadian` (030–033), transcrita de los ejemplos del anexo RADIAN |
| `calendario/` | `Festivo`: los festivos de Colombia. **Propia, se actualiza a mano cada año** (ver su README) |

```bash
python manage.py cargar_catalogos
```

Carga y actualiza a la vez: por cada lista hace un único *upsert* por id, que
crea los códigos que faltan y pone al día el nombre (y las columnas extra) de
los que ya están. Se repite sin miedo tras cambiar una lista; no borra nada.
Los municipios se enlazan a su departamento por los dos primeros dígitos del
código DANE. Las pruebas cargan solo lo que usan: `cargar([Moneda])`.

**Id fijo.** La columna `id` de las tablas no es autoincremental
(`ElementoCatalogo.id` es un `BigIntegerField`): el id de cada código es el de
la columna `id` de su `.gc`, y crear una fila sin id falla. Hay quien manda el
id y no el código —torio envía la moneda, la unidad de medida, el tipo de
identificación, el país, el departamento y el municipio por id—, así que no
puede depender del orden de carga de cada base.

**Añadir un código o una lista.** Un código nuevo lleva su `id` a mano, el
siguiente libre de esa lista, y `cargar_catalogos` se niega a cargar una fila
sin él. Un id puesto no se cambia nunca. Un catálogo nuevo es un `.gc` (con su
columna `id`) y una línea en `LISTAS`.

## Correcciones aplicadas sobre los archivos oficiales

- **`TiposEventos.gc`**: el archivo original venía con XML malformado — en 5
  filas faltaba el cierre `</SimpleValue>` (aparecía `...DIANSimpleValue>` en
  lugar de `...DIAN</SimpleValue>`). Se corrigió el cierre de etiqueta sin
  alterar el contenido. La corrección es inequívoca.

> Si se actualiza la caja de herramientas DIAN, volver a aplicar esta
> corrección si el archivo sigue viniendo con el mismo defecto.

## Añadidos sobre los archivos oficiales

> Al reemplazar una lista por una versión nueva de la caja de herramientas hay
> que volver a poner todo lo de este apartado, con los mismos ids.

- **Columna `id`** en las 16 listas oficiales que se cargan (las 15 de aquí y
  `documento-soporte/TipoDocumento-2.1.gc`), declarada `Use="optional"` y
  colocada en cada fila justo después de `code`. Los valores son los ids que ya
  circulaban, los de la base con la que torio está integrado: no se deducen de
  nada.

  **Caso particular: `TipoIdentificacion-2.1.gc`.** Ahí el id es el propio
  código (`13` cédula → id 13, `31` NIT → id 31), porque todos sus códigos son
  numéricos y así quien integra puede mandar el código DIAN como id. Un código
  nuevo en esa lista lleva como id su código, no el siguiente libre.

  Igual en **`Concepto de Reclamo.gc`** (añadida el 2026-10-06, sin ids
  previos que respetar): `01` → id 1, …, `04` → id 4.

- **Filas que la DIAN no publica en ninguna lista:**
  - `TipoDocumento-2.1.gc`: `20` Documento equivalente P.O.S. (id 9).
  - `TipoIdentificacion-2.1.gc`: `47` PEP (id 47), que el anexo de nómina
    (numeral 5.2.1) usa y solo traía la lista del documento soporte.

- **`Municipio-2.1.gc`**: se le agregó la columna `codigo_postal` (declarada
  `Use="optional"`, para no tocar el contrato de las dos oficiales) con el
  código postal de la cabecera de cada uno de los 1.122 municipios.

  Hace falta porque el `cbc:PostalZone` del XML es obligatorio y la DIAN no da
  forma de resolverlo: su lista `CodigoPostal1.gc` son 3.760 códigos sueltos,
  sin municipio al que atarlos, y del código DANE no se deduce el postal —solo
  comparten los dos primeros dígitos, el departamento—.

  **Origen del dato.** Dataset *Códigos Postales Nacionales* de Servicios
  Postales Nacionales (4-72) en el portal de Datos Abiertos, identificador
  `ixig-z8b5`:
  <https://www.datos.gov.co/Ordenamiento-Territorial/C-digos-Postales-Nacionales/ixig-z8b5>
  (descargado el 2026-09-11). Son 3.681 códigos, cada uno con su municipio DANE
  y su tipo, urbano o rural.

  **Reconstrucción.** El archivo publicado trae los números con formato es-CO,
  así que los códigos llegan rotos: Abejorral aparece como `55.03`, que no es un
  código sino `055030` con el cero final comido. Se recuperan multiplicando por
  1.000 y rellenando a seis dígitos por la izquierda; igual con el código del
  municipio, que llega sin su cero inicial. Que la recuperación es correcta lo
  dice el cruce contra `CodigoPostal1.gc`, la lista de códigos válidos de la
  propia DIAN: los 1.122 caen dentro. Lo comprueba
  `CodigoPostalDeMunicipiosTests` en `apps/catalogos/tests.py`.

  **Cuál es el principal.** El dataset no lo dice, así que se toma el código
  urbano más bajo del municipio, que es el de la cabecera. Once municipios
  —corregimientos departamentales de Amazonas, Vaupés y Guainía— no tienen
  ninguna fila urbana; para esos se toma el más bajo de todos. Solo en 4
  municipios los dos criterios dan códigos distintos.

> Si se actualiza la caja de herramientas DIAN, volver a añadir esta columna:
> el archivo oficial no la trae ni la va a traer.
