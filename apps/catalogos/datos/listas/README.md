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
