# Notas del Anexo Técnico RADIAN v1.1

> Resolución DIAN No. 000085 (08/ABR/2022). Fuente: *Caja de herramientas
> RADIAN 1.1*. Resume lo que hace falta para los eventos del ciclo comercial
> (030–034). No reemplaza el anexo oficial.
>
> Complementa a [anexo-tecnico.md](anexo-tecnico.md). El plan de
> implementación está en [recepcion.md](recepcion.md), sección «Eventos RADIAN».

El PDF (482 págs.) no se versiona. Del zip se guardaron los ejemplos 030–034 en
`apps/dian/datos/ejemplos/radian/`. Los XSD son idénticos a los que ya están.

## 1. Qué cubre el anexo y qué no

El anexo RADIAN detalla campo a campo **los eventos de título valor (035–051)**:
inscripción, endosos, aval, mandato, pagos, limitaciones, protesto y
transferencias. Los eventos **030–034 no tienen tabla de campos aquí**:

- el anexo trae sus **ejemplos XML**, la **fórmula del CUDE**, los **WS**, las
  **reglas de tiempo de la firma** (DC24a–e) y el **correo** para entregarlos;
- sus reglas campo a campo (las `AAxx` propias de cada evento) están en el
  **Anexo Técnico de Factura Electrónica 1.9**, que no está en el repo
  (ver `docs/anexo-tecnico.md`). Mientras tanto, los ejemplos y el encabezado
  común (§3) bastan para construirlos; los rechazos de la DIAN en habilitación
  dirán lo que falte.

Según el anexo, los eventos se generan para facturas **a crédito** que se
quieren usar como título valor (§7.2). Y: **una vez hay aceptación (033 o 034),
la factura ya no admite notas crédito ni débito** (§7.2).

## 2. Los cinco eventos

| Código | `cbc:Description` (ejemplo) | Emite | Recibe (`ReceiverParty`) | Persona (`IssuerParty`) |
|---|---|---|---|---|
| 030 | Acuse de recibo de Factura Electrónica de Venta | Adquiriente | Facturador | Sí |
| 031 | Reclamo de la Factura Electrónica de Venta | Adquiriente | Facturador | No |
| 032 | Recibo del bien y/o prestación del servicio | Adquiriente | Facturador | Sí |
| 033 | Aceptación expresa | Adquiriente | Facturador | No |
| 034 | Aceptación Tácita | **Facturador** | **DIAN** (NIT 800197268) | No |

- **031**: el concepto va en atributos del código —
  `<cbc:ResponseCode name="Mercancía no entregada totalmente" listID="02">031</cbc:ResponseCode>`—,
  con la lista `Concepto de Reclamo.gc` (01 documento con inconsistencias,
  02 mercancía no entregada totalmente, 03 parcialmente, 04 servicio no prestado).
- **030 y 032**: `cac:DocumentResponse/cac:IssuerParty/cac:Person` con `cbc:ID`
  (`@schemeName` = tipo de documento, p. ej. 13), `cbc:FirstName`,
  `cbc:FamilyName`, `cbc:JobTitle` y `cbc:OrganizationDepartment`.
- **034**: lleva una `cbc:Note` obligatoria con el juramento. Sin mandatario:
  *«Manifiesto bajo la gravedad de juramento que transcurridos 3 días hábiles
  siguientes a la fecha de recepción de la mercancía o del servicio en la
  referida factura de este evento, el adquirente [Razón social] identificado con
  NIT [XXXX] no manifestó expresamente la aceptación o rechazo de la referida
  factura, ni reclamó en contra de su contenido.»*
- Con mandatario, `cbc:Note[1]` = `<mandatario> OBRANDO EN NOMBRE Y
  REPRESENTACION DE <mandante>`. En modalidad software propio no aplica.

### Reglas de tiempo (validadas sobre `xades:SigningTime`)

| Regla | El evento… | …se rechaza si se firma |
|---|---|---|
| DC24a | 030 | antes de la fecha de la factura |
| DC24b | 032 | antes del 030 |
| DC24c | 033 | pasados 3 días hábiles del 032 |
| DC24e | 034 | antes de 3 días hábiles del 032 |
| DC24 | todos | con fecha posterior a la del sistema de la DIAN |

De ahí el orden: **030 → 032 → (033 o 031) dentro de 3 días hábiles; si no,
034**. La DIAN además rechaza un evento repetido y uno incoherente con los
anteriores (p. ej. aceptación después de reclamo) (§8.7.1).

## 3. Encabezado común del `ApplicationResponse`

```xml
<cbc:UBLVersionID>UBL 2.1</cbc:UBLVersionID>
<cbc:CustomizationID>1</cbc:CustomizationID>   <!-- tipo de operación; 1 en los ejemplos 030–034 -->
<cbc:ProfileID>DIAN 2.1: ApplicationResponse de la Factura Electrónica de Venta</cbc:ProfileID>
<cbc:ProfileExecutionID>2</cbc:ProfileExecutionID>   <!-- 1 producción, 2 pruebas -->
<cbc:ID>ACR0021</cbc:ID>                             <!-- consecutivo propio, 1–50 caracteres -->
<cbc:UUID schemeID="2" schemeName="CUDE-SHA384">…</cbc:UUID>
<cbc:IssueDate>2020-12-12</cbc:IssueDate>
<cbc:IssueTime>18:30:37-05:00</cbc:IssueTime>
<cac:SenderParty>/<cac:ReceiverParty>   <!-- cac:PartyTaxScheme: RegistrationName, CompanyID (NIT con DV en @schemeID), TaxScheme -->
<cac:DocumentResponse>
  <cac:Response><cbc:ResponseCode>030</cbc:ResponseCode><cbc:Description>…</cbc:Description></cac:Response>
  <cac:DocumentReference>
    <cbc:ID>SETG980000358</cbc:ID>
    <cbc:UUID schemeName="CUFE-SHA384">…</cbc:UUID>
    <cbc:DocumentTypeCode>01</cbc:DocumentTypeCode>
  </cac:DocumentReference>
  <cac:IssuerParty>…</cac:IssuerParty>   <!-- solo 030 y 032 -->
</cac:DocumentResponse>
```

- **`cbc:ID`** (AAD05): numeración del emisor del evento, consecutiva y que no
  se repita **por tipo de evento**. No hay resolución de numeración.
- **`sts:DianExtensions`**: `InvoiceSource` (CO), `SoftwareProvider`
  (`ProviderID` y `SoftwareID`), `SoftwareSecurityCode`,
  `AuthorizationProvider` (800197268) y `QRCode`. Como en la factura, pero sin
  `InvoiceControl`.
- **`SoftwareSecurityCode`**: `SHA-384(SoftwareID + PIN + cbc:ID del evento)`,
  igual que en la factura.
- **`QRCode`** (en los ejemplos): `https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUFE de la factura>`.
- **Firma**: XAdES-EPES, la misma política que la factura.

## 4. CUDE del evento (§12.1.1)

```
CUDE = SHA-384(Num_DE + Fec_Emi + Hor_Emi + NitFE + DocAdq + ResponseCode
               + ID + DocumentTypeCode + Software-PIN)
```

| Campo | XPath |
|---|---|
| Num_DE | `cbc:ID` del evento |
| Fec_Emi / Hor_Emi | `cbc:IssueDate` / `cbc:IssueTime` (con `-05:00`) |
| NitFE | `cac:SenderParty/…/cbc:CompanyID` (quien genera el evento) |
| DocAdq | `cac:ReceiverParty/…/cbc:CompanyID` (quien lo recibe) |
| ResponseCode | código del evento |
| ID | `cac:DocumentReference/cbc:ID` (prefijo y número de la factura) |
| DocumentTypeCode | `cac:DocumentReference/cbc:DocumentTypeCode` |
| Software-PIN | PIN del software, no va en el XML |

**Vector de prueba (comprobado):**
`12019-04-3019:48:50-05:0099998888800197268030FE1230111111` →
`0d91ba25b01f5e7dbda870a11b274501d3a62a73e91932c473c86c93f12a142a2ac45876efcde3e679024a01c0be41f9`.

## 5. Web services (§8.7 y §8.8)

Mismo endpoint y mismo sobre WS-Security que la factura (`apps/dian/soap.py`).

- **`SendEventUpdateStatus`** — síncrono. Parámetro `contentFile`: ZIP en
  base64 con **un solo** `ApplicationResponse`. Responde lo mismo que
  `SendBillSync`: `IsValid`, `StatusCode` (00 procesado, 99 errores),
  `ErrorMessage` (reglas), `XmlBase64Bytes` (el `ApplicationResponse` de la
  DIAN) y `XmlDocumentKey` (el CUDE).
- **`GetStatusEvent`** — síncrono. Parámetro `trackId`: el **CUFE de la
  factura**. Devuelve en `XmlBase64Bytes` un `ApplicationResponse` con los
  eventos registrados.

Antes de validar el contenido, la DIAN comprueba que el CUFE existe, que
`cbc:ResponseCode` viene y que la versión es «UBL 2.1»; después, la ventana de
fechas, que el evento no se repita y la coherencia con los anteriores.

## 6. Entrega a la contraparte por correo (§10.1)

- **Asunto**: `Evento;<número de la factura>;<NIT de quien genera el evento>;<su nombre>;<número del evento>;<código del evento>;<línea de negocio opcional>`.
  Ejemplo: `Evento;FEV500;10203040;Facturador Ejemplo;APPR10;030;ContabilidadBog`.
- **Adjunto**: un solo `.zip` con un `AttachedDocument` que contiene el
  `ApplicationResponse` del evento y el de la DIAN. Opcionalmente, el PDF.
- Máximo **2 MB** por envío. El cuerpo es libre.

Así hay que mandarle el evento al proveedor. (Así llegarían también al buzón de
recepción los eventos de los clientes del emisor, con asunto `Evento;`, pero
ese sentido está fuera del alcance de nobelio: ver `docs/recepcion.md`.)

## 7. Listas de la caja

- `Eventos-2.1.gc`: solo 035–051 (título valor). **Los 030–034 no están en
  ninguna lista de la caja**; las de la caja FE (`EventoDocumento-2.1.gc`,
  `TiposEventos.gc`) están desactualizadas. El catálogo se arma a mano con los
  cinco códigos y las descripciones de los ejemplos.
- `Concepto de Reclamo.gc` (caja FE, ya en `apps/catalogos/datos/listas/`):
  conceptos del 031.
- El resto (endosos, mandatos, falta de aceptación, tipos de pago) es de título
  valor y no aplica.
