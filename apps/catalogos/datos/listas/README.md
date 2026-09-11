# Listas de valores DIAN (Genericode `.gc`)

Copia de las listas de valores oficiales de la DIAN incluidas en la
*Caja de herramientas FE V19 (v2026)*. Se versionan en el repo para que el
proyecto sea autocontenido y la carga de catálogos sea reproducible.

Se leen con `apps/catalogos/genericode.py`.

## Correcciones aplicadas sobre los archivos oficiales

- **`TiposEventos.gc`**: el archivo original venía con XML malformado — en 5
  filas faltaba el cierre `</SimpleValue>` (aparecía `...DIANSimpleValue>` en
  lugar de `...DIAN</SimpleValue>`). Se corrigió el cierre de etiqueta sin
  alterar el contenido. La corrección es inequívoca.

> Si se actualiza la caja de herramientas DIAN, volver a aplicar esta
> corrección si el archivo sigue viniendo con el mismo defecto.

## Añadidos sobre los archivos oficiales

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
