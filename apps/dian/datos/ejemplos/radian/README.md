# Ejemplificaciones oficiales — Eventos RADIAN

XML de ejemplo de la *Caja de herramientas RADIAN 1.1* (Resolución 000085 del
08/ABR/2022), carpeta `Ejemplificaciones/XMLs de ejemplo`. Solo los cinco
eventos del ciclo comercial; los de título valor (035–051) no se copiaron.

| Archivo | Original | Qué muestra |
|---------|----------|-------------|
| `030-acuse-de-recibo.xml` | `030-…/Ejemplificacion Acuse_ReciboFEV.xml` | Evento completo, con `cac:IssuerParty/cac:Person` (quien recibe) |
| `031-reclamo.xml` | `031-…/Ejemplificacion Reclamo_FEV.xml` | El concepto del reclamo en `cbc:ResponseCode/@listID` y `@name` |
| `032-recibo-del-bien.xml` | `032-…/Ejemplificacion Recibo_B&S.xml` | Con `cac:IssuerParty/cac:Person` |
| `033-aceptacion-expresa.xml` | `033-…/Ejemplificacion Aceptacion_expresa_FEV.xml` | Sin persona |
| `034-aceptacion-tacita.xml` | `034-…/Ejemplificacion Aceptacion_Tacita_FEV.xml` | El `ReceiverParty` es la DIAN (800197268) y la nota de juramento |

Los cinco validan contra `apps/dian/datos/xsd/maindoc/UBL-ApplicationResponse-2.1.xsd`.
Los XSD del zip son **byte a byte los mismos** que ya están en
`apps/dian/datos/xsd/` (`maindoc/` y `common/`), así que no se copiaron.

> **El CUDE de estos ejemplos no sirve como vector de prueba.** En el 030, la
> composición de `cbc:Note[1]` no da el `cbc:UUID` que trae el archivo, y del
> 031 al 033 el CUDE es el mismo en los tres. Su `SoftwareSecurityCode` sí
> cuadra con `cbc:Note[2]` (SoftwareID + PIN 20191 + número). El vector bueno es el del
> numeral 12.1.1.1 del anexo, que sí cuadra (ver
> [docs/anexo-radian.md](../../../../../docs/anexo-radian.md)).

El PDF del anexo (482 págs.) no se versiona, igual que los demás.
