# Ejemplificaciones oficiales — Documento Soporte

XML de ejemplo de la *Caja de herramientas Documento Soporte Validación Previa*,
carpeta `Version 1.1/Ejemplificaciones` (Resolución 000167 del 30/DIC/2021).

Se versionan porque son la referencia práctica del orden de los elementos UBL:
el anexo describe campo por campo, pero el orden que exige el XSD solo se ve aquí.
Los nombres se normalizaron (los originales traían acentos mal codificados).

| Archivo | Original | Qué muestra |
|---------|----------|-------------|
| `documento-soporte-residente.xml` | `DocumentoSoporte-OperacionConResidente.xml` | DS completo, `CustomizationID=10`, con retenciones, descuentos y cargos |
| `documento-soporte-moneda-extranjera.xml` | `Ejemplificación Doc Soporte modena diferente a COP.xml` | El mismo en USD, con `cac:PaymentExchangeRate` |
| `nota-de-ajuste.xml` | `NotaDeAjuste.xml` | Nota de ajuste tipo 95 sobre el DS anterior |

> **Dos puntos en los que el ejemplo no sigue la norma** (detalle en
> [docs/anexo-documento-soporte.md](../../../../../docs/anexo-documento-soporte.md)):
> `nota-de-ajuste.xml` emite `cbc:InvoiceTypeCode` dentro de un `CreditNote`
> —la regla NSAD12 dice `cbc:CreditNoteTypeCode`—, y el `sts:QRCode` de los DS
> trae el bloque multilínea de la representación gráfica en vez de la URL sola
> que exige la regla DSAB36. Se conservan sin tocar: son los archivos oficiales.

Validan contra los XSD que ya están en `apps/dian/datos/xsd/`
(`UBL-Invoice-2.1.xsd` y `UBL-CreditNote-2.1.xsd`): el documento soporte no trae
esquemas propios. Se compararon los 19 XSD de ambas cajas de herramientas y son
idénticos byte a byte.
