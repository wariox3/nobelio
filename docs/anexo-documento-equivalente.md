# Notas del Anexo Técnico DIAN — Documento Equivalente Electrónico v1.0

Fuente: **Resolución 000165 del 01/11/2023**, "Anexo Técnico de documento
equivalente electrónico – Versión 1.0" (1546 páginas), y la
`Caja-de-herramientas-Doc-Equivalentes-V1-3`.

Este resumen cubre **solo el tiquete de máquina registradora con sistema P.O.S.**
(numeral 8.2). El anexo define trece documentos equivalentes más —servicios
públicos, peajes, transporte terrestre, tiquete aéreo, juegos localizados,
espectáculos públicos, bolsa de valores, cine, extracto…— y sus notas de ajuste
(8.12). Nada de eso está implementado ni analizado aquí.

## 1. Esto **sí** es UBL

La diferencia que más trabajo ahorra frente a la nómina: el documento
equivalente es un `Invoice` UBL 2.1 corriente, en el mismo namespace que la
factura de venta, con `sts:DianExtensions`, `cac:AccountingSupplierParty`,
`cac:InvoiceLine`, `cac:TaxTotal` y `cac:LegalMonetaryTotal`.

Comprobado, no supuesto: los XSD de la caja de herramientas
(`UBL-Invoice-2.1.xsd`, `DIAN_UBL_Structures.xsd`, `UBL-CommonAggregateComponents-2.1.xsd`)
son **byte a byte idénticos** a los que ya tenemos en
`apps/dian/datos/xsd/`, y la ejemplificación oficial `Ejemplo POS.xml`
valida contra nuestro `UBL-Invoice-2.1.xsd` sin un solo error.

O sea: donde la nómina obligó a una pila paralela entera (otra raíz, otro
namespace, otro modelo, otro constructor, otro cálculo de identificador), el POS
es **un tipo más dentro del pipeline de facturación que ya existe**.

## 2. Cabecera del POS

Lo que distingue al POS de una factura de venta, en la ejemplificación oficial:

| Elemento | Factura de venta | Documento equivalente POS |
|---|---|---|
| Raíz | `Invoice` | `Invoice` (igual) |
| `cbc:CustomizationID` | `10` | `10` |
| `cbc:ProfileID` | `DIAN 2.1: Factura Electrónica de Venta` | `DIAN 2.1: Documento Equivalente POS` |
| `cbc:InvoiceTypeCode` | `01` | **`20`**, con `@name="Factura tipo punto de venta POS"` |
| `cbc:UUID` `@schemeName` | `CUFE-SHA384` | **`CUDE-SHA384`** |
| Identificador | CUFE (clave técnica) | **CUDE (PIN del software)** |
| `sts:InvoiceControl` | sí | **sí** — el POS también se numera con resolución |
| Adquiriente | el receptor real | **`Consumidor Final`, ID `222222222222`** |
| `ext:UBLExtensions` | DianExtensions + firma | DianExtensions + **3 extensiones propias** + firma |

> El `@name="Factura tipo punto de venta POS"` del `InvoiceTypeCode` **no lo
> valida ninguna regla**: cero menciones a `@name` en las 318 reglas del numeral
> 10.2. Es adorno de la ejemplificación. Se emite igualmente, porque reproducir
> la ejemplificación es lo que resolvió el ZE02 y no cuesta nada.

### Las tres extensiones propias

Además del `sts:DianExtensions` de siempre, el POS cuelga tres extensiones más
de `ext:UBLExtensions`, todas con la misma forma de pares `Name`/`Value` y —
curiosamente— en el namespace de `Invoice-2`, no en uno propio:

1. **`FabricanteSoftware` / `InformacionDelFabricanteDelSoftware`** —
   `NombreApellido`, `RazonSocial`, `NombreSoftware`. Describen a **quien hizo
   el software, no a quien emite**, así que son de la plataforma y salen de
   `DIAN_FABRICANTE_*` en `settings`. `SoftwareDian` conserva los tres campos
   como excepción por emisor, para el obligado que use software propio.
2. **`BeneficiosComprador` / `InformacionBeneficiosComprador`** (numeral 8.2.1.1) —
   `Codigo`, `NombresApellidos`, `Puntos`. Programa de fidelización.
3. **`PuntoVenta` / `InformacionCajaVenta`** (numeral 8.2.1.2) —
   `PlacaCaja`, `UbicaciónCaja`, `Cajero`, `TipoCaja`, `CódigoVenta`, `SubTotal`.

Ojo con `UbicaciónCaja` y `CódigoVenta`: llevan **tilde** en el `Name`. Es
literal del anexo, no una errata de este documento.

**Las tres son obligatorias.** Las reglas DEPD11 y DEPD21, las dos de tipo
*Rechazo*, dicen que el POS "requiere que existan declarados al menos tres nodos
obligatorios" y listan cinco: `sts:DianExtensions`, `ds:Signature`,
`InformacionDelFabricanteDelSoftware`, `InformacionVeneficiosComprador` e
`InformacionCajaVenta`. No hay condicionales: un POS sin el programa de puntos
se rechaza igual que uno sin firma.

Cada par `Name`/`Value` tiene además su propia regla de Rechazo sobre el literal
exacto del `Name` (DEAB41–46, DEPD15–20, DEPD25–36).

> ⚠️ **`Veneficios` con V.** El anexo se contradice a sí mismo: la columna
> *Regla* dice `InformacionBeneficiosComprador` (con B) y la columna *XPath* de
> las mismas reglas dice `InformacionVeneficiosComprador` (con **V**), igual que
> el texto de DEPD11 y DEPD21. La ejemplificación oficial usa **B**.
>
> Se emite con **B**, la de la ejemplificación, por el precedente del ZE02: el
> validador de la DIAN se comportó como si estuviera construido contra la
> ejemplificación y no contra la letra del anexo. Si un POS se rechaza por
> DEPD13 o DEPD14 ("no es informado el grupo") teniendo el grupo puesto, esta
> errata es el primer sospechoso y la prueba es cambiar la B por la V.

### Una divergencia conocida con la ejemplificación

El `cbc:UUID` de la ejemplificación lleva cuatro atributos y el nuestro dos:

```
oficial:  schemeAgencyID="195" schemeAgencyName="CO, DIAN (…)" schemeID="2" schemeName="CUDE-SHA384"
nuestro:                                                       schemeID="2" schemeName="CUDE-SHA384"
```

Se deja así **a propósito**: los emite `_cabecera` de `ConstructorUBL`, que es
común a todos los tipos, y nuestras facturas y documentos soporte llevan años
saliendo sin esos dos atributos y la DIAN los acepta. Añadirlos solo para el
P.O.S. exigiría tocar la base y cambiaría el XML de todo lo demás.

Si un P.O.S. se rechaza señalando el `UUID`, esto es lo primero que hay que
probar — y entonces se hace con un atributo de clase nuevo, no modificando
`_cabecera`.

## 3. CUDE — ya lo tenemos calculado

```
CUDE = SHA-384(
    NumFac + FecFac + HorFac + ValFac +
    CodImp1 + ValImp1 +      # 01 IVA
    CodImp2 + ValImp2 +      # 04 INC
    CodImp3 + ValImp3 +      # 03 ICA
    ValTot + NitFE + NumAdq + SoftwarePIN + TipoAmbiente
)
```

Es **exactamente** la composición que ya implementa `calcular_cude` en
`apps/dian/identificadores.py` — la misma que usan las notas crédito/débito y el
documento soporte. No hay identificador nuevo que escribir.

Detalle a respetar: `ValFac` es `cac:LegalMonetaryTotal/cbc:LineExtensionAmount`,
no el `TaxExclusiveAmount`. Los impuestos no referenciados van en `0.00`.

## 3 bis. Reglas de validación del POS (numeral 10.2)

**318 reglas**: 234 de *Rechazo* y 80 de *Notificación*. Las que fijan la
identidad del documento:

| Regla | Tipo | Qué exige |
|---|---|---|
| DEAD01 | Rechazo | `cbc:UBLVersionID` = `UBL 2.1` |
| DEAD02 | Rechazo | `cbc:CustomizationID` con un valor válido de tipo de operación |
| DEAD03 | Rechazo | `cbc:ProfileID` = `DIAN 2.1: Documento Equivalente POS`, literal |
| DEAD12a/b | Rechazo | `cbc:InvoiceTypeCode` = `20` para el POS |
| DEAI01 | Rechazo | El tipo `08` (contingencia) exige `cac:AdditionalDocumentReference` |
| DEAB05a/b | Rechazo | La autorización de numeración debe existir y ser de este emisor |
| DEPD11, DEPD21 | Rechazo | Las cinco extensiones obligatorias (ver §2) |

Existe un **tipo 08, contingencia**, además del 20. No está en el alcance de
esta implementación, pero explica por qué DEAD12a habla de "tipos permitidos"
en plural.

## 4. Nombre de los archivos — **formato nuevo** (numeral 8.13.5)

```
ds nnnnnnnnnn ppp aa dddddddd .xml
```

- `ds` — documento equivalente electrónico (`ncs` para su nota de ajuste,
  `ars` para el ApplicationResponse soporte, `z` para el zip)
- `nnnnnnnnnn` — NIT sin DV, 10 dígitos con ceros a la izquierda
- `ppp` — **código de 3 dígitos que la DIAN asigna al proveedor tecnológico**
- `aa` — dos últimos dígitos del año
- `dddddddd` — consecutivo de archivos enviados, **8 dígitos hexadecimales**,
  reiniciado a `00000001` cada 1 de enero

Ejemplo del anexo: `ds08001972680002000000001.xml`.

Esto **no encaja en ninguna de las dos funciones que tenemos**:

| | Formato | Consecutivo |
|---|---|---|
| `nombre_archivo_dian` (factura) | prefijo + NIT(10) + consec(8) | decimal |
| `nombre_archivo_nomina` | prefijo + NIT(10) + aa + consec(8) | hex |
| **documento equivalente** | prefijo + NIT(10) + **ppp** + aa + consec(8) | hex |

Es la de nómina más el segmento `ppp`. Hace falta una tercera función, y el
`ppp` es un dato que hoy no guardamos en ninguna parte.

## 5. Web service — el mismo de facturación

Numerales 9.2 a 9.5: **`SendBillSync`** para el envío síncrono, `GetStatus` por
CUDE, `GetStatusZip` por ZipKey y **`SendTestSetAsync`** para el ambiente de
habilitación. Los mismos cuatro que ya habla `apps/dian/soap.py`.

## 6. Habilitación — sí, tiene Set de Pruebas

Numeral 4: el sujeto "deberá surtir el proceso de habilitación […] emitiendo el
número de documentos equivalentes electrónicos y la(s) nota(s) de ajuste
requeridos por el sistema", validados y aprobados por la DIAN, y solo entonces
pasa a producción en operación.

> ⚠️ **No repetir el error de la nómina.** Dar por hecho que la nómina no tenía
> Set de Pruebas costó los rechazos 92, NIE017, NIE033 y ZE02 (ver
> `docs/anexo-nomina.md` §9 bis). Aquí el anexo lo dice explícitamente: hay
> habilitación propia, con su propio `TestSetId`, y probablemente su propio
> software en el catálogo de participantes. Se confirma en el portal antes de
> emitir, no después.

## 7. Qué ya tenemos y qué falta

**Se reutiliza sin tocar nada:**

- `ConstructorUBL` (`apps/dian/ubl.py:165`): raíz, cabecera, partes, líneas,
  `agrupar_impuestos`, `LegalMonetaryTotal`, `sts:DianExtensions`, QR.
- `calcular_cude` y `calcular_codigo_seguridad_software`.
- La firma XAdES (`apps/dian/firma.py`), el SOAP (`apps/dian/soap.py`), los
  servicios de emitir/enviar/consultar, el `AttachedDocument`, el
  almacenamiento en B2, los estados y los `DocumentoError`.
- El modelo `Documento` completo, con su numeración y su resolución.
- Los XSD: **son los mismos**, no hay que añadir ninguno.

**Hecho** (fases 1 a 4):

- `DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS` con `codigo_dian = "20"`, y
  alta en `CODIGOS_CON_RESOLUCION`.
- `SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE`, los tres campos del fabricante y
  el `codigo_proveedor_tecnologico`; `Emisor.ambiente_documento_equivalente` y
  `habilitado_documento_equivalente`.
- `ConstructorDocumentoEquivalentePOS` (`apps/dian/ubl.py`), con las tres
  extensiones propias.
- `DocumentoPOS`, satélite 1:1 con todo lo de la venta: la caja (placa,
  ubicación y tipo), el cajero, el código de venta, el subtotal y los datos del
  comprador. **La caja no tiene maestro**: se valoró y se descartó, porque la
  DIAN no la contrasta contra ningún registro y un maestro obligaba al punto de
  venta a conocer un id nuestro y a darse de alta antes de vender. Como columnas
  del documento, además, lo guardado es lo emitido: mover una caja de pasillo no
  reescribe los tiquetes de ayer.
- `nombre_archivo_documento_equivalente` —reproduce el ejemplo del anexo— y
  `ConsecutivoArchivoDocumentoEquivalente`, su numerador anual.
- El enganche del envío: `_software_activo` elige el software por tipo de
  documento, `Documento.save` toma el ambiente de
  `ambiente_documento_equivalente`, y `_marcar_habilitacion_superada` sabe cuál
  de las tres banderas marcar. El P.O.S. sale por `SendTestSetAsync` mientras
  su Set de Pruebas no esté aceptado y por `SendBillSync` después, con la misma
  bifurcación que ya tenía la factura.

- La API: el bloque `pos` anidado en `POST /api/documentos/documento/` (con su
  validación cruzada: obligatorio en el P.O.S., prohibido en los demás).

**Falta:**
- La resolución de numeración propia del P.O.S. y el TestSetId de su
  habilitación (fase 7), que son datos, no código.


El adquiriente **no** se fuerza a "Consumidor Final": el documento lo declara
como cualquier otro tipo y quien emite decide. Cuando los campos del comprador
de `DocumentoPOS` van vacíos, la extensión los toma del adquiriente, así que un
tiquete a consumidor final sale correcto sin informar nada aparte.

## 7 bis. Notas de ajuste al documento equivalente (numeral 8.12)

**No se parecen a la nota de ajuste de nómina**, que reemplaza o elimina el
documento entero. Estas son de **crédito y débito**, como las de la factura:
corrigen por diferencia, llevan `cac:DiscrepancyResponse` y referencian el
documento ajustado.

| | Nota de ajuste crédito | Nota de ajuste débito |
|---|---|---|
| Raíz | `CreditNote` | `DebitNote` |
| `cbc:ProfileID` | `DIAN 2.1: Nota de ajuste crédito al documento equivalente` | `DIAN 2.1: Nota de ajuste débito al documento equivalente` |
| Tipo | `CreditNoteTypeCode` = `94` | sin elemento de tipo (el UBL `DebitNote` no lo define) |
| Identificador | CUDE | CUDE |
| Reglas | 260 con prefijo `NA` | prefijo `NAD` |
| Archivo | `ncs`, compartiendo el consecutivo de la familia | igual |

Tres detalles que sí importan:

- **El `CustomizationID` es `20`, y aquí no significa el tipo de operación**
  como en la factura, sino **a qué documento equivalente se refiere la nota**
  (numeral 16.4.2): 20 el tiquete P.O.S., 25 el de cine, 40 el de peajes…
- El `cbc:UUID` referenciado en el `BillingReference` es un **CUDE**, no un
  CUFE.
- **No llevan las tres extensiones del P.O.S.** Sus ejemplificaciones traen
  solo `sts:DianExtensions` y la firma.

Los literales del `ProfileID` se comprobaron carácter a carácter contra
`NDAC.xml` y `NDADxml.xml`: **no llevan espacio final**, a diferencia de lo que
aparentaba el de la nota de ajuste del documento soporte y que costó un rechazo
NSAD03.

El concepto de corrección sale de una lista propia (numeral 16.6), compartida
por las dos notas: coincide en números con las otras y no en redacción —aquí el
`2` anula un documento equivalente—.

> Dos asimetrías del anexo que se reproducen tal cual, no se corrigen: la nota
> de **crédito** emite `cbc:DocumentTypeCode` = `20` dentro de la referencia y
> la de débito no; y la regla NAAB14 pide `sts:CreditNoteSource` donde la
> ejemplificación —y nosotros— emitimos `sts:InvoiceSource`. Esa segunda es de
> tipo *Notificación*, no de rechazo.

## 8. Lo que NO cubre este documento

- Los otros doce documentos equivalentes (SPD, peajes, transporte, aéreo,
  juegos, espectáculos, bolsa, cine, extracto…).
- La tabla de reglas de validación campo a campo (numeral 10.2, ~50 páginas
  solo para el POS). Es lo que hay que leer antes de emitir de verdad: es el
  equivalente de las reglas NIE/NIAE de nómina, y es donde viven los rechazos.
