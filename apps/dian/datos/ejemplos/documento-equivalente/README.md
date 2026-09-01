# Ejemplificaciones oficiales — Documento Equivalente Electrónico

XML de ejemplo de la *Caja de Herramientas Doc-Equivalentes V1-3*, carpeta
`Ejemplificaciones/XMLs de ejemplo` (Resolución 000165 del 01/NOV/2023, Anexo
Técnico v1.0).

| Archivo | Original | Qué muestra |
|---------|----------|-------------|
| `documento-equivalente-pos.xml` | `Ejemplo POS.xml` | Tiquete P.O.S. completo: `InvoiceTypeCode` 20, CUDE, y las tres extensiones propias |
| `documento-equivalente-con-nota-ajuste.xml` | `Ejemplo Doc Equivalente Con referencia a una Nota Ajuste.xml` | Cómo referencia un documento equivalente su nota de ajuste |

A diferencia de las ejemplificaciones de nómina, **estas sí traen datos
plausibles** (importes que cuadran, un NIT real de pruebas, impuestos
calculados), así que sirven para leer la estructura *y* para entender los
valores. Lo que no traen es la firma: su `ext:UBLExtensions` tiene las cuatro
extensiones del documento pero no la del `ds:Signature`.

`documento-equivalente-pos.xml` **valida contra
`apps/dian/datos/xsd/maindoc/UBL-Invoice-2.1.xsd`**, el que ya usábamos para las
facturas — comprobado, sin un solo error.

Del zip no se copió nada más, por el mismo criterio que en nómina: los XSD de
`XSD/maindoc/` y `XSD/common/` son **byte a byte los mismos** que ya están en
`apps/dian/datos/xsd/` (comprobados `UBL-Invoice-2.1.xsd`,
`DIAN_UBL_Structures.xsd` y `UBL-CommonAggregateComponents-2.1.xsd`), y el PDF
del anexo (1546 páginas) no se versiona.

El resumen de lo que hace falta para implementarlo está en
[docs/anexo-documento-equivalente.md](../../../../../docs/anexo-documento-equivalente.md).
