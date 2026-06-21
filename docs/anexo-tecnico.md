# Notas del Anexo Técnico DIAN — Factura Electrónica de Venta v1.9

> Resolución DIAN No. 000165 (01/NOV/2023). Fuente: caja de herramientas FE V19 (v2026).
> Este documento resume los puntos del anexo necesarios para implementar el servicio.
> No reemplaza el anexo oficial (753 págs.); es una guía de implementación.

## 1. Ambiente y perfiles

- `cbc:ProfileExecutionID` = **Tipo de Ambiente**: `1` = Producción, `2` = Habilitación/Pruebas.
- `cbc:UBLVersionID` = `UBL 2.1`.
- `cbc:CustomizationID` = identifica la operación (ver listas `TipoOperacionF/NC/ND`).
- `cbc:ProfileID` = `DIAN 2.1`.

## 2. Tipos de documento (`cbc:InvoiceTypeCode`)

| Código | Documento |
|--------|-----------|
| 01 | Factura electrónica de Venta |
| 02 | Factura de exportación |
| 03 | Documento electrónico de transmisión (contingencia) |
| 04 | Factura de venta (tipo 04) |
| 91 | Nota Crédito |
| 92 | Nota Débito |

(El documento soporte y la nómina usan estructuras/atributos propios.)

## 3. CUFE — Código Único de Factura Electrónica

- Algoritmo: **SHA-384** sobre la concatenación (sin separadores) de los campos en orden exacto.
- Va en `/Invoice/cbc:UUID`, con `@schemeName = "CUFE-SHA384"`.
- Valores numéricos: punto decimal, **2 decimales truncados**, sin separador de miles ni símbolo.
- NITs: sin puntos, sin guiones, **sin dígito de verificación**.

```
CUFE = SHA-384(
    NumFac +      # /Invoice/cbc:ID  (prefijo + número)
    FecFac +      # /Invoice/cbc:IssueDate          (YYYY-MM-DD)
    HorFac +      # /Invoice/cbc:IssueTime          (HH:MM:SS-05:00, con GMT)
    ValFac +      # /Invoice/cac:LegalMonetaryTotal/cbc:LineExtensionAmount
    "01" +        # CodImp1 fijo (IVA)
    ValImp1 +     # valor IVA;  0.00 si no aplica
    "04" +        # CodImp2 fijo (INC)
    ValImp2 +     # valor INC;  0.00 si no aplica
    "03" +        # CodImp3 fijo (ICA)
    ValImp3 +     # valor ICA;  0.00 si no aplica
    ValTot +      # /Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount
    NitOFE +      # /Invoice/cac:AccountingSupplierParty/.../cbc:CompanyID
    NumAdq +      # /Invoice/cac:AccountingCustomerParty/.../cbc:CompanyID
    ClTec +       # Clave técnica del rango (de la consulta de numeración; NO va en el XML)
    TipoAmbiente  # 1 o 2
)
```

**Ejemplo oficial (verificable):**
```
Composición = 3232000001292019-01-1610:53:10-05:001500000.0001285000.00040.00030.001785000.00700085371800199436693ff6f2a553c3646a063436fd4dd9ded03114711
CUFE (hex)  = 8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33381030bcd4c3c3f156c506ed5908f9276f5bd9b4
```
> Sirve como caso de prueba de la implementación: misma entrada ⇒ mismo hash.

## 4. CUDE — Código Único de Documento Electrónico

- Mismo algoritmo y orden que el CUFE, **pero** en lugar de `ClTec` se usa el **PIN del software** (`Software-PIN`).
- Se usa en Notas, Documento Soporte y ApplicationResponse.
- `@schemeName = "CUDE-SHA384"`.

```
CUDE = SHA-384(NumFac + FecFac + HorFac + ValFac + 01 + ValImp1 + 04 + ValImp2 +
               03 + ValImp3 + ValTot + NitOFE + NumAdq + SoftwarePIN + TipoAmbiente)
```

## 5. Código de seguridad del software (`sts:SoftwareSecurityCode`)

```
SoftwareSecurityCode = SHA-384(IdSoftware + Pin + NroDocumento)
```
- `IdSoftware`: identificador del software registrado en DIAN.
- `Pin`: PIN del software (no va en el XML en claro).
- `NroDocumento`: número del documento (NumFac).

## 6. Firma digital — XAdES-EPES

- Estándar: **XAdES-EPES** (XML Advanced Electronic Signature, forma básica + política de firma).
- La firma `ds:Signature` va dentro de `ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent`.
- Política de firma (`xades:SignaturePolicyIdentifier`):
  - `SigPolicyId/Identifier` = URL de la política DIAN v2
    (`https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf`).
  - `SigPolicyHash`: `ds:DigestMethod` = SHA-256 + `ds:DigestValue` (hash del PDF de la política).
- Algoritmos de firma admitidos: RSA con SHA-256 / SHA-384 / SHA-512.
- Referencias firmadas: el documento (enveloped), `KeyInfo` y `SignedProperties`.
- XSD relevantes: `XSD/common/UBL-XAdESv132-2.1.xsd` y `UBL-XAdESv141-2.1.xsd`.

## 7. Código QR (representación gráfica)

Contenido textual del QR (claves `Detalle: valor`, una por línea):

```
NumFac: <cbc:ID>
FecFac: <cbc:IssueDate>
HorFac: <cbc:IssueTime>  (con GMT)
NitFac: <NIT facturador>   (sin puntos ni guiones)
DocAdq: <id adquirente>    (sin puntos ni guiones)
ValFac: <LineExtensionAmount>
ValIva: <TaxAmount con TaxScheme/ID = 01>
ValOtroIm: <sumatoria TaxAmount con ID != 01>
ValTolFac: <PayableAmount>
CUFE/CUDE: <cbc:UUID>
QRCode: https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUFE>
```
- En habilitación, el dominio es `catalogo-vpfe-hab.dian.gov.co`.
- Tamaño mínimo del QR: **2 cm**. Debe ir en **todas** las páginas de la representación gráfica.

## 8. Estructura UBL del Invoice (orden de elementos)

Árbol de alto nivel (confirmado contra `Ejemplificaciones/.../Generica.xml`):

```
Invoice
├── ext:UBLExtensions
│   ├── ext:UBLExtension → ext:ExtensionContent → sts:DianExtensions
│   │     ├── sts:InvoiceControl (InvoiceAuthorization, AuthorizationPeriod, AuthorizedInvoices: Prefix/From/To)
│   │     ├── sts:InvoiceSource (IdentificationCode = CO)
│   │     ├── sts:SoftwareProvider (ProviderID, SoftwareID)
│   │     ├── sts:SoftwareSecurityCode
│   │     ├── sts:AuthorizationProvider (AuthorizationProviderID = NIT DIAN 800197268)
│   │     └── sts:QRCode
│   └── ext:UBLExtension → ext:ExtensionContent → ds:Signature   (XAdES-EPES)
├── cbc:UBLVersionID / CustomizationID / ProfileID / ProfileExecutionID
├── cbc:ID / UUID(@schemeName) / IssueDate / IssueTime / InvoiceTypeCode / Note
├── cbc:DocumentCurrencyCode / LineCountNumeric
├── cac:InvoicePeriod
├── cac:BillingReference            (en notas: referencia a la factura)
├── cac:AccountingSupplierParty     (emisor / OFE)
├── cac:AccountingCustomerParty     (adquirente)
├── cac:PaymentMeans / PaymentTerms
├── cac:TaxTotal[]                  (por cada impuesto: TaxAmount + TaxSubtotal/TaxCategory/TaxScheme)
├── cac:LegalMonetaryTotal          (LineExtensionAmount, TaxExclusiveAmount, TaxInclusiveAmount, PayableAmount)
└── cac:InvoiceLine[]               (líneas: cantidad, precio, impuestos por línea, item)
```

Namespaces principales:
- `fe`/default: `urn:oasis:names:specification:ubl:schema:xsd:Invoice-2`
- `cac`: `...:CommonAggregateComponents-2`
- `cbc`: `...:CommonBasicComponents-2`
- `ext`: `...:CommonExtensionComponents-2`
- `sts`: `dian:gov:co:facturaelectronica:Structures-2-1`
- `xades`: `http://uri.etsi.org/01903/v1.3.2#`
- `ds`: `http://www.w3.org/2000/09/xmldsig#`

## 9. Web Services DIAN

- WSDL Habilitación: `https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl`
- WSDL Producción: `https://vpfe.dian.gov.co/WcfDianCustomerServices.svc?wsdl`
- Operaciones clave: `SendTestSetAsync` (set de pruebas/habilitación), `SendBillSync`,
  `SendBillAsync`, `GetStatus`, `GetStatusZip`.
- Seguridad: **WS-Security** (firma del SOAP con el mismo certificado).
- El documento se envía **comprimido en ZIP** y codificado en Base64.
- Respuesta: `ApplicationResponse` (XML) con estado de validación.
- Ver `Guia-Herramienta-para-el-Consumo-de-Web-Services.pdf`.

## 10. Listas de valores (Genericode `.gc`)

Disponibles en `Listas de valores/*.gc` para alimentar la app `catalogos`:
`TipoDocumento`, `TipoOrganizacion`, `TipoResponsabilidad`, `TipoIdFiscal`,
`Departamentos`, `Municipio`, `Paises`, `CodigoPostal`, `UnidadesMedida`,
`FormasPago`, `MediosPago`, `TipoMoneda`, `TipoImpuesto`, `TarifaImpuestoIVA/INC/ReteFuente/ReteIVA`,
`AlgoritmoCUFE`, `AlgoritmoCUDE`, `TipoAmbiente`, `ConceptoNotaCredito`, `ConceptoNotaDebito`, etc.

---

### Casos de prueba derivados (para tests automáticos)
1. **CUFE**: la entrada del numeral 3 debe producir el hash documentado.
2. **CUDE**: la entrada del numeral 11.4.1 (pág. 661) produce su hash documentado.
3. **Truncamiento**: los valores monetarios se truncan (no se redondean) a 2 decimales.
