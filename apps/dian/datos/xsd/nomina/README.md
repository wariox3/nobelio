# XSD propios de Nómina Electrónica

De la *Caja de Herramientas Nómina Electrónica V1.0* (Resolución 000013 del
11/FEB/2021), carpeta `XSD`. A diferencia del documento soporte —que reutiliza
los XSD de factura— la nómina **sí trae esquemas propios**: su XML no es UBL,
solo lo es la sección de la firma.

| Archivo | Elemento raíz | Namespace |
|---------|---------------|-----------|
| `NominaIndividualElectronicaXSDV1.0.6.xsd` | `NominaIndividual` | `dian:gov:co:facturaelectronica:NominaIndividual` |
| `NominaIndividualDeAjusteElectronicaXSDV1.0.6.xsd` | `NominaIndividualDeAjuste` | `dian:gov:co:facturaelectronica:NominaIndividualDeAjuste` |

Van en esta subcarpeta a propósito: ambos importan
`../common/UBL-CommonExtensionComponents-2.1.xsd` para la firma, y desde aquí esa
ruta relativa cae justo en el `common/` que ya existe. Los diez XSD de la carpeta
`Schemes` del zip son idénticos byte a byte a los que ya estaban, así que no se
copió ninguno.
