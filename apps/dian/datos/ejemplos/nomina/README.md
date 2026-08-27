# Ejemplificaciones oficiales — Nómina Electrónica

XML de ejemplo de la *Caja de Herramientas Nómina Electrónica V1.0*, carpeta
`Ejemplificaciones` (Resolución 000013 del 11/FEB/2021, Anexo Técnico v1.0).

| Archivo | Original | Qué muestra |
|---------|----------|-------------|
| `nomina-individual.xml` | `Nomina Individual Electronica V1.0.2.xml` | Esqueleto del `NominaIndividual` completo |
| `nota-de-ajuste-nomina.xml` | `Nomina Individual De Ajuste Electronica V1.0.2.xml` | `NominaIndividualDeAjuste` con sus dos bloques, `Reemplazar` y `Eliminar` |

> **No son documentos realistas: son plantillas.** Todos los valores son
> marcadores (`A`, `0`, `0.00`, `9999-12-31`) y traen *todos* los elementos
> opcionales a la vez. Sirven para el **orden y el anidamiento** de los
> elementos, no para los datos ni para verificar el CUNE.

Validan contra los XSD de `apps/dian/datos/xsd/nomina/` salvo por un punto: su
`<ext:UBLExtensions>` viene vacío y el XSD exige al menos un `ext:UBLExtension`.
Es esperable —ahí va la firma XAdES, que las ejemplificaciones no traen—; el
XML que emitamos sí lo lleva.

Del zip no se copió nada más: los diez XSD de `Schemes/` son **byte a byte los
mismos** que ya están en `apps/dian/datos/xsd/common/` (comprobados los diez), y
el PDF del anexo (6,8 MB) no se versiona, igual que los de factura y documento
soporte. El resumen de lo que hace falta para implementar está en
[docs/anexo-nomina.md](../../../../../docs/anexo-nomina.md).
