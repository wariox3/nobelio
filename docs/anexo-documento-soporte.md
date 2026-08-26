# Notas del Anexo Técnico DIAN — Documento Soporte v1.1

> Resolución DIAN No. 000167 (30/DIC/2021). Fuente: *Caja de herramientas Documento
> Soporte Validación Previa*, carpeta `Version 1.1`.
> Este documento resume los puntos del anexo (267 págs.) necesarios para implementar
> la emisión. No reemplaza el anexo oficial; es una guía de implementación.
>
> Complementa a [anexo-tecnico.md](anexo-tecnico.md), que cubre la factura de venta
> y las notas crédito/débito. **Aquí solo se documenta lo que difiere.**

El PDF de la resolución (5,6 MB) no se versiona, igual que el anexo de factura.
Del zip sí se guardaron en el repo las dos cosas que el código necesita:

- **Listas de valores DS**: `apps/catalogos/datos/listas/documento-soporte/`
- **Ejemplificaciones oficiales**: `apps/dian/datos/ejemplos/documento-soporte/`

Los XSD del DS son **byte a byte los mismos** que ya están en
`apps/dian/datos/xsd/` (se comprobaron los 19 archivos): el DS no trae esquema
propio, reutiliza `UBL-Invoice-2.1.xsd` y `UBL-CreditNote-2.1.xsd`.

---

## 1. Quién es quién (lo que cambia todo)

En el documento soporte **el que emite es el comprador**. La DIAN los nombra así:

| Sigla | Quién es | Dónde va en el UBL |
|-------|----------|--------------------|
| **SNO** | Sujeto No Obligado a facturar: el **vendedor** | `cac:AccountingSupplierParty` |
| **ABS** | Adquiriente de Bienes y/o Servicios: el **obligado** que genera y firma | `cac:AccountingCustomerParty` |

Es decir, los roles UBL van **invertidos** respecto a la factura: quien firma el
documento aparece como *customer*, no como *supplier*. El `sts:SoftwareProvider`,
el `sts:InvoiceControl` y el certificado son todos del **ABS**.

## 2. Cabecera

| Elemento | Documento soporte | Nota de ajuste |
|----------|-------------------|----------------|
| Raíz | `Invoice` | `CreditNote` |
| `cbc:CustomizationID` | `10` o `11` (ver §3) | `10` u `11` |
| `cbc:ProfileID` | `DIAN 2.1: documento soporte en adquisiciones efectuadas a no obligados a facturar.` | `DIAN 2.1: Nota de ajuste al documento soporte en adquisiciones efectuadas a sujetos no obligados a expedir factura o documento equivalente ` |
| Código de tipo | `cbc:InvoiceTypeCode` = `05` | `cbc:CreditNoteTypeCode` = `95` |
| `cbc:UUID/@schemeName` | `CUDS-SHA384` | `CUDS-SHA384` |
| `cbc:UUID/@schemeID` | ambiente (1 ó 2) | ambiente |

Los literales del `ProfileID` son de longitud fija 82 (reglas DSAD03 / NSAD03) y
se comparan exactos. **El de la nota de ajuste termina en espacio** —así aparece
tanto en la regla como en la ejemplificación oficial—; el punto final del DS
también es parte del literal.

> ⚠️ **Discrepancia del anexo consigo mismo.** La ejemplificación
> `nota-de-ajuste.xml` emite `cbc:InvoiceTypeCode` dentro de un `CreditNote`, que
> ni siquiera valida contra el XSD. La regla normativa NSAD12 dice
> `/CreditNote/cbc:CreditNoteTypeCode`, y el numeral 14.1.1.2 lo confirma. **Vale
> la regla, no el ejemplo.**

## 3. `cbc:CustomizationID` — procedencia del vendedor

No es el "tipo de operación" de la factura: aquí indica de dónde es el SNO
(lista `TipoOperacion-2.1.gc`, numeral 16.1.4.1).

| Código | Procedencia |
|--------|-------------|
| `10` | Residente |
| `11` | No Residente |

Gobierna qué campos de las partes son obligatorios: la dirección física
(`cac:PhysicalLocation`) y el código postal se exigen **solo cuando vale `10`**.
Con `11` el vendedor se identifica con la lista `TipoIdFiscal-2.1.gc`
(pasaporte, cédula de extranjería, NIT de otro país, PEP…).

## 4. CUDS — Código Único de Documento Soporte

**No es el CUDE.** Comparte el algoritmo (SHA-384) pero **la composición es más
corta**: lleva **un solo** impuesto —el IVA— donde el CUFE/CUDE lleva tres pares
(IVA, INC, ICA). Reutilizar `calcular_cude()` produciría un hash equivocado.

```
CUDS = SHA-384(
    NumDS +       # /Invoice/cbc:ID  (prefijo + consecutivo)
    FecDS +       # /Invoice/cbc:IssueDate            (YYYY-MM-DD)
    HorDS +       # /Invoice/cbc:IssueTime            (HH:MM:SS-05:00)
    ValDS +       # /Invoice/cac:LegalMonetaryTotal/cbc:LineExtensionAmount
    "01" +        # CodImp fijo (IVA) — único impuesto de la composición
    ValImp +      # /Invoice/cac:TaxTotal/cbc:TaxAmount;  0.00 si no hay IVA
    ValTot +      # /Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount
    NumSNO +      # /Invoice/cac:AccountingSupplierParty/.../cbc:CompanyID   ← vendedor
    NITABS +      # /Invoice/cac:AccountingCustomerParty/.../cbc:CompanyID   ← obligado
    SoftwarePIN + # PIN del software; NO va en el XML
    TipoAmbiente  # /Invoice/cbc:ProfileExecutionID
)
```

Ojo al orden de los dos identificadores: **primero el vendedor, después el
obligado**. Como el rol UBL va invertido, en nuestro modelo eso es primero la
contraparte y después el emisor —lo contrario que en la factura—.

En la nota de ajuste los XPath son los mismos con raíz `/CreditNote`.

**Ejemplo oficial (verificado: la entrada reproduce el hash exacto):**
```
Composición = 00000000012020-10-2414:04:35-05:0015000.000119.0016350.009003730768355990123451
CUDS (hex)  = bf4bb6920d5054ac065ddb7e6df0398e63e3ba2ff29cb341edd7d46ee8f2ea1802f84aaca91a19a24623e5e3baff3a71
```
Desglose: `NumDS=0000000001`, `FecDS=2020-10-24`, `HorDS=14:04:35-05:00`,
`ValDS=15000.00`, `CodImp=01`, `ValImp=19.00`, `ValTot=16350.00`,
`NumSNO=900373076`, `NITABS=8355990`, `PIN=12345`, `Amb=1`.

## 5. Extensiones DIAN (`sts:DianExtensions`)

Mismo bloque que la factura, **incluido `sts:InvoiceControl`**: el documento
soporte **sí lleva resolución de numeración autorizada**, con su propio prefijo y
rango. La nota-1 del numeral 14.1.1.2 lo dice explícitamente: las verificaciones
del rango se hacen contra la numeración de `InvoiceTypeCode=05`, o sea que es una
resolución distinta de la de facturación.

`sts:QRCode` (regla DSAB36) es **solo la URL**:

```
https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUDS>
```
(en habilitación, `catalogo-vpfe-hab.dian.gov.co`).

> ⚠️ La ejemplificación trae en su lugar un bloque multilínea
> (`N°DocSoporte=…`, `NumSNO=…`, `NITABS=…`, `PIN:…`, `CUDS=…`, `URL=…`). Ese
> formato de clave/valor es el que corresponde a la **representación gráfica**
> (numeral del QR impreso), no al elemento `sts:QRCode`. Para el XML manda la
> regla: la URL sola.

Los datos que sí van en el QR **impreso** del PDF: `NumDS`, `FecDS`, `HorDS`,
`NumSNO`, `DocAdq` (NIT del ABS), `ValDS`, `ValIva`, `ValTolDS`, `CUDS`, `QRCode`.

## 6. Las partes son mucho más simples que en la factura

Confirmado contra `documento-soporte-residente.xml`: los bloques de parte **no
llevan** `cac:PartyName`, ni `cac:PartyLegalEntity`, ni `cac:PartyIdentification`,
ni `cac:Contact`, ni `cac:Person`. Solo:

```
cac:AccountingSupplierParty          ← SNO (vendedor)
├── cbc:AdditionalAccountID          (1 = persona jurídica, 2 = natural)
└── cac:Party
    ├── cac:PhysicalLocation/cac:Address   (solo si CustomizationID = 10)
    └── cac:PartyTaxScheme
        ├── cbc:RegistrationName
        ├── cbc:CompanyID  (@schemeID = DV, @schemeName = tipo de identificación)
        ├── cbc:TaxLevelCode           ("O-23;O-47" — responsabilidades separadas por ;)
        └── cac:TaxScheme (cbc:ID + cbc:Name; "ZZ"/"No aplica" en el SNO)

cac:AccountingCustomerParty          ← ABS (el obligado, quien firma)
├── cbc:AdditionalAccountID
└── cac:Party
    └── cac:PartyTaxScheme            (sin dirección: el ABS no la informa)
```

## 7. Retenciones: `cac:WithholdingTaxTotal`

El DS las lleva, a nivel de documento **y** de línea, en un elemento aparte de
`cac:TaxTotal`. Es el mecanismo natural del documento soporte (el comprador
practica ReteIVA y ReteRenta sobre la compra al no obligado). La lista
`TipoImpuesto` del DS solo contempla tres tributos: `01` IVA, `05` ReteIVA,
`06` ReteRenta. Tarifas en `TarifaImpuestoReteRenta-2.1.gc`.

El anexo de factura no usa este elemento, así que el modelo actual de impuestos
no tiene dónde guardarlo.

## 8. Otros elementos presentes en las ejemplificaciones

- `cbc:Note` a nivel de documento (descripción libre).
- `cac:InvoicePeriod` **por línea**, con `cbc:DescriptionCode` + `cbc:Description`
  (p. ej. `1` / "Por operación").
- `cac:AllowanceCharge` con `cbc:AllowanceChargeReasonCode` y
  `cbc:MultiplierFactorNumeric` (porcentaje), a nivel de documento y de línea.
- `cac:Item` con `PackSizeNumeric`, `BrandName`, `ModelName`,
  `SellersItemIdentification` y `StandardItemIdentification`.
- `cac:PaymentMeans` con `cbc:PaymentID` (texto libre sobre el medio de pago).
- `cac:PaymentExchangeRate` cuando `DocumentCurrencyCode` ≠ COP
  (`SourceCurrencyCode`, `TargetCurrencyCode`, `CalculationRate`, `Date`).

## 9. Nota de ajuste al documento soporte (tipo 95)

Estructura de nota, como las notas crédito de factura:

- `cac:DiscrepancyResponse`: `ReferenceID` (número del DS), `ResponseCode`
  (lista `ConceptoNotaAjuste-2.1.gc`) y `Description`.
- `cac:BillingReference/cac:InvoiceDocumentReference`: `ID`, `UUID`
  (`@schemeName="CUDS-SHA384"`) e `IssueDate` del DS ajustado.

Conceptos de ajuste (`ConceptoNotaAjuste`), distintos en redacción de los de la
nota crédito de factura:

| Código | Concepto |
|--------|----------|
| 1 | Devolución parcial de los bienes y/o no aceptación parcial del servicio |
| 2 | Anulación del documento soporte |
| 3 | Rebaja o descuento parcial o total |
| 4 | Ajuste de precio |
| 5 | Otros |

## 10. Listas de valores propias del DS

En `apps/catalogos/datos/listas/documento-soporte/` (ver el README de esa carpeta
para el detalle de cuáles se copiaron y por qué):

| Archivo | Para qué |
|---------|----------|
| `TipoDocumento-2.1.gc` | Solo los códigos `05` y `95` |
| `TipoOperacion-2.1.gc` | `CustomizationID`: 10 Residente / 11 No Residente |
| `ConceptoNotaAjuste-2.1.gc` | `ResponseCode` de la nota de ajuste |
| `AlgoritmoCUDS-2.1.gc` | Literal `CUDS-SHA384` |
| `TipoIdFiscal-2.1.gc` | Identificación del SNO no residente |
| `TarifaImpuestoReteRenta-2.1.gc` | Tarifas de retención en la fuente |
| `TipoResponsabilidad-2.1.gc` | Añade `O-48` y `O-49` (ver README) |
| `FormaGeneracionTransmision.gc` | Forma de generación/transmisión |
| `TiposRespuesta.gc` | `02` validado / `04` rechazado |

## 11. Ejemplificaciones oficiales

En `apps/dian/datos/ejemplos/documento-soporte/`:

| Archivo | Qué muestra |
|---------|-------------|
| `documento-soporte-residente.xml` | DS completo, `CustomizationID=10`, con retenciones y descuentos |
| `documento-soporte-moneda-extranjera.xml` | El mismo en USD, con `cac:PaymentExchangeRate` |
| `nota-de-ajuste.xml` | Nota de ajuste tipo 95 sobre el DS anterior |

Son la referencia práctica del orden de los elementos. Recordar las dos
discrepancias documentadas arriba (§2 y §5): en esos dos puntos el ejemplo no
sigue la norma.

---

### Casos de prueba derivados

1. **CUDS**: la composición del §4 debe producir el hash documentado
   (verificado: coincide).
2. **Roles**: en un DS, el NIT del emisor tiene que salir en
   `AccountingCustomerParty` y el del vendedor en `AccountingSupplierParty`.
3. **XSD**: el DS valida contra `UBL-Invoice-2.1.xsd` y la nota de ajuste contra
   `UBL-CreditNote-2.1.xsd`, sin esquemas nuevos.
