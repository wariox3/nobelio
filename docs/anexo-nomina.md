# Notas del Anexo Técnico DIAN — Nómina Electrónica v1.0

> Resolución DIAN No. 000013 (11/FEB/2021). Fuente: *Caja de Herramientas Nómina
> Electrónica V1.0*.
> Este documento resume los puntos del anexo (269 págs.) necesarios para
> implementar la emisión. No reemplaza el anexo oficial; es una guía de
> implementación.
>
> Complementa a [anexo-tecnico.md](anexo-tecnico.md) (factura y notas) y a
> [anexo-documento-soporte.md](anexo-documento-soporte.md). **Aquí solo se
> documenta lo que difiere.**

El PDF del anexo (6,8 MB) no se versiona, igual que los otros dos. Del zip sí se
guardó lo que el código necesita:

- **XSD propios**: `apps/dian/datos/xsd/nomina/`
- **Ejemplificaciones**: `apps/dian/datos/ejemplos/nomina/`

Los diez XSD de la carpeta `Schemes` del zip son **byte a byte los mismos** que
ya están en `apps/dian/datos/xsd/common/` (se compararon los diez), así que no se
copiaron. **No hay listas `.gc`**: las tablas de códigos de nómina viven solo en
el PDF y están transcritas abajo (§7).

---

## 1. Esto no es UBL

Es la diferencia que gobierna todo lo demás. El documento soporte reutilizaba
`Invoice` y solo cambiaba el contenido; la nómina **no es un documento UBL**:

| | Factura / DS | Nómina |
|---|---|---|
| Raíz | `Invoice` / `CreditNote` | `NominaIndividual` / `NominaIndividualDeAjuste` |
| Namespace | `urn:oasis:...:Invoice-2` | `dian:gov:co:facturaelectronica:NominaIndividual` |
| Estructura | elementos `cbc:`/`cac:` con texto | **atributos** sobre elementos propios |
| XSD | UBL 2.1 | propios (`apps/dian/datos/xsd/nomina/`) |
| Identificador | CUFE / CUDE / CUDS | **CUNE** |
| Extensión DIAN | `sts:DianExtensions` | no existe; sus datos son elementos propios |
| Resolución de numeración | sí (factura y DS) | **no**: el prefijo lo elige el emisor (NIE010) |
| Contraparte | adquiriente / vendedor | **Trabajador** (empleado), más `Empleador` y `ProveedorXML` |

Lo único UBL es la firma: `ext:UBLExtensions` con la XAdES, idéntica a la de
factura (numeral 3.6 y §7 del anexo). El resto del XML es propio.

## 2. Estructura del `NominaIndividual`

Orden del XSD, que es el que hay que respetar:

```
NominaIndividual
├── ext:UBLExtensions            ← la firma XAdES (el XSD exige ≥1 extensión)
├── Novedad          @CUNENov               (opcional: novedad contractual)
├── Periodo          @FechaIngreso @FechaRetiro @FechaLiquidacionInicio
│                    @FechaLiquidacionFin @TiempoLaborado @FechaGen
├── NumeroSecuenciaXML  @CodigoTrabajador @Prefijo @Consecutivo @Numero
├── LugarGeneracionXML  @Pais @DepartamentoEstado @MunicipioCiudad @Idioma
├── ProveedorXML     @RazonSocial @NIT @DV @SoftwareID @SoftwareSC (+ nombres)
├── CodigoQR
├── InformacionGeneral  @Version @Ambiente @TipoXML @CUNE @EncripCUNE
│                       @FechaGen @HoraGen @PeriodoNomina @TipoMoneda @TRM
├── Notas
├── Empleador        @RazonSocial @NIT @DV @Pais @DepartamentoEstado
│                    @MunicipioCiudad @Direccion (+ nombres)
├── Trabajador       @TipoTrabajador @SubTipoTrabajador @AltoRiesgoPension
│                    @TipoDocumento @NumeroDocumento @TipoContrato @Sueldo
│                    @SalarioIntegral @CodigoTrabajador @LugarTrabajo* (+ nombres)
├── Pago             @Forma @Metodo @Banco @TipoCuenta @NumeroCuenta
├── FechasPagos      FechaPago+   (el modelo guarda una sola: `Nomina.fecha_pago`)
├── Devengados       Basico, Transporte, HEDs/HENs/HRNs/HEDDFs/HRDDFs/HENDFs/
│                    HRNDFs, Vacaciones, Primas, Cesantias, Incapacidades,
│                    Licencias, Bonificaciones, Auxilios, HuelgasLegales,
│                    OtrosConceptos, Compensaciones, BonoEPCTVs, Comisiones,
│                    PagosTerceros, Anticipos, Dotacion, ApoyoSost, Teletrabajo,
│                    BonifRetiro, Indemnizacion, Reintegro
├── Deducciones      Salud, FondoPension, FondoSP, Sindicatos, Sanciones,
│                    Libranzas, PagosTerceros, Anticipos, OtrasDeducciones,
│                    PensionVoluntaria, RetencionFuente, AFC, Cooperativa,
│                    EmbargoFiscal, PlanComplementarios, Educacion, Reintegro, Deuda
├── Redondeo
├── DevengadosTotal
├── DeduccionesTotal
└── ComprobanteTotal   (= DevengadosTotal − DeduccionesTotal)
```

`@Version` es el equivalente del `ProfileID` y es un literal exacto (NIE022):
`V1.0: Documento Soporte de Pago de Nómina Electrónica`.

> ⚠️ El literal de la nota de ajuste (NIAE022) aparece **de dos formas** en el
> anexo: `"V1.0: Nota de Ajuste de Documento Soporte de Pago de Nómina
> Electrónica"` en la tabla de campos y `" V1.0: … "` —con espacios delante y
> detrás— en la tabla de reglas. Es el mismo tropiezo que costó un rechazo
> NSAD03 en la nota de ajuste del documento soporte: se emite **sin** los
> espacios y, si la DIAN lo rechaza, se prueba con ellos.

## 3. CUNE

```
CUNE = SHA-384(
    NumNE +        # NumeroSecuenciaXML/@Numero (prefijo + consecutivo)
    FecNE +        # InformacionGeneral/@FechaGen   (YYYY-MM-DD)
    HorNE +        # InformacionGeneral/@HoraGen    (HH:MM:SS-05:00)
    ValDev +       # DevengadosTotal
    ValDed +       # DeduccionesTotal
    ValTolNE +     # ComprobanteTotal
    NitNE +        # Empleador/@NIT   (sin DV)
    DocEmp +       # Trabajador/@NumeroDocumento
    TipoXML +      # 102 nómina, 103 nota de ajuste
    SoftwarePIN +  # PIN del software; NO va en el XML
    TipAmb         # InformacionGeneral/@Ambiente
)
```

Va en `@CUNE`, con `@EncripCUNE` = `CUNE-SHA384`. Truncado a dos decimales y sin
separadores de miles, igual que el CUFE. En la nota de ajuste el XPath cambia de
raíz: `/NominaIndividualDeAjuste/{Reemplazar|Eliminar}/InformacionGeneral/@CUNE`.

> ⚠️ **El ejemplo del anexo (numeral 8.1.1.3) no reproduce su propio hash.** Se
> probó la composición documentada y variantes razonables (hora con y sin GMT,
> valores sin decimales, otros órdenes e incluso todas las permutaciones de los
> once campos) y ninguna da el CUNE publicado. En el CUFE, el CUDE y el CUDS los
> ejemplos oficiales sí cuadraban, así que aquí **no hay caso de prueba fiable**:
> el cálculo solo se confirma contra la DIAN en habilitación.

## 4. Código de seguridad del software

`SoftwareSC = SHA-384(IdSoftware + PIN + NroDocumento)` (numeral 8.2), donde
`NroDocumento` es `NumeroSecuenciaXML/@Numero`. Es **exactamente** la fórmula de
factura: `identificadores.calcular_codigo_seguridad_software()` sirve tal cual.

## 5. QR

`CodigoQR` (NIE021) es solo la URL, como en el DS:

```
https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUNE>
```

## 6. Nota de ajuste (`NominaIndividualDeAjuste`)

No es una nota crédito: es un **reemplazo o una eliminación** del documento
anterior. `TipoNota` (1 Reemplazar / 2 Eliminar) manda, y el XML tiene dos
bloques hermanos:

- `Reemplazar`: repite **entero** el documento (Periodo, partes, Devengados,
  Deducciones, totales…) y añade `ReemplazandoPredecesor` con `@NumeroPred`,
  `@CUNEPred` y `@FechaGenPred`.
- `Eliminar`: solo la cabecera (sin Trabajador, Devengados ni totales) y
  `EliminandoPredecesor` con los mismos tres datos. Ojo: ahí el
  `InformacionGeneral` va **recortado** (sin `PeriodoNomina`, `TipoMoneda` ni
  `TRM`) y el `NumeroSecuenciaXML` sin `@CodigoTrabajador`.

Los dos bloques viven en el namespace `…:NominaIndividualDeAjuste`, no en el de
la nómina: el cuerpo se arma con el mismo código pero los elementos cuelgan de
otro namespace.

O sea: el "ajuste" no lleva diferencias sino el documento corregido completo. No
hay `DiscrepancyResponse` ni conceptos de corrección.

## 7. Listas de valores (§5 del anexo; no hay `.gc`)

**PeriodoNomina**: 1 Semanal · 2 Decenal · 3 Catorcenal · 4 Quincenal ·
5 Mensual · 6 Otro

**TipoContrato**: 1 Término fijo · 2 Término indefinido · 3 Obra o labor ·
4 Aprendizaje · 5 Prácticas o pasantías

**TipoTrabajador**: 01 Dependiente · 02 Servicio doméstico · 04 Madre comunitaria
· 12 Aprendiz SENA lectiva · 18 Funcionario público sin tope de IBC · 19 Aprendiz
SENA productiva · 21 Estudiante de postgrado en salud · 22 Profesor de
establecimiento particular · 23 Estudiante solo riesgos laborales · 30
Dependiente de entidad pública con régimen especial en salud · 31 Cooperado de
CTA · 47 Dependiente de entidad del SGP · 51 Tiempo parcial · 54 Pre pensionado
de entidad en liquidación · 56 Pre pensionado con aporte voluntario a salud ·
58 Estudiante en prácticas del sector público

**SubTipoTrabajador**: 00 No aplica · 01 Dependiente pensionado por vejez activo

**Porcentaje de hora extra o recargo**: 1 HED 25 % · 2 HEN 75 % · 3 HRN 35 % ·
4 HEDDF 100 % · 5 HRDDF 75 % · 6 HENDF 150 % · 7 HRNDF 110 %

**Tipo de incapacidad**: 1 Común · 2 Profesional · 3 Laboral

**TipoXML**: 102 `NominaIndividual` · 103 `NominaIndividualDeAjuste`

**TipoNota**: 1 Reemplazar · 2 Eliminar

**Ambiente**: 1 Producción · 2 Pruebas (igual que factura)

**TipoDocumento** (identificación del trabajador): 11 Registro civil ·
12 Tarjeta de identidad · 13 Cédula · 21 Tarjeta de extranjería · 22 Cédula de
extranjería · 31 NIT · 41 Pasaporte · 42 Documento extranjero · 47 PEP ·
50 NIT de otro país · 91 NUIP (**solo para el empleado**: no existe en el RUT).

País, departamento, municipio, idioma y moneda salen de las mismas listas ISO
que ya usa la factura, y la identificación del trabajador reutiliza
`catalogos.TipoIdentificacion`: de los once códigos del numeral 5.2.1 solo
faltaba el `47` (PEP), porque el `91` (NUIP) ya venía en la lista de factura.

Las cuatro listas propias y ese `47` los siembra la migración
`catalogos/0006_datos_nomina`; `cargar_catalogos` no interviene porque no hay
`.gc` que leer. Todas quedan expuestas en `/api/catalogos/` como los demás
catálogos (`periodo-nomina`, `tipo-contrato`, `tipo-trabajador`,
`subtipo-trabajador`).

## 8. Nombre de los archivos (§3.3–3.5)

| Qué | Formato | Ejemplo |
|-----|---------|---------|
| XML de nómina | `nie` + NIT(10, con ceros) + `aa` + 8 hex | `nie0800197268200000000C.xml` |
| XML de nota de ajuste | `niae` + NIT(10) + `aa` + 8 hex | `niae0800197268200000000C.xml` |
| ZIP de envío | `z` + NIT(10) + `aa` + 8 hex | `z0800197268200000000C.zip` |

El consecutivo hexadecimal es **de archivos enviados**, se reinicia cada 1 de
enero y no tiene nada que ver con el consecutivo del documento. El repo ya tiene
`nombre_archivo_dian`, pero con los prefijos de factura.

## 9. Web service

Mismo servicio y mismo certificado de transmisión que factura
(`WcfDianCustomerServices`), con una operación nueva:

- **`SendNominaSync`**: síncrona, un solo documento por ZIP. Es la operación de
  producción, la que se usa **una vez superada la habilitación**.
- **`SendTestSetAsync`**: la misma de factura, y también la de la habilitación
  de nómina. La nómina tiene su propio Set de Pruebas, con su `TestSetId` y su
  software, separados de los de facturación: se obtienen en el portal al
  asociar el modo de operación de nómina electrónica.
- **`GetStatus`** / **`GetStatusZip`**: por CUNE lo enviado en síncrono, por
  ZipKey la entrega asíncrona al Set de Pruebas.

> **Corregido el 2026-08-28.** Este apartado afirmaba que no había
> `SendTestSetAsync` para nómina y que la habilitación se hacía contra
> `SendNominaSync`. Es falso, y costó una tanda de rechazos: enviar por la
> operación de producción sin estar habilitado devuelve la regla 92 ("El Emisor
> del Documento no se encuentra Habilitado"), con NIE017, NIE033 y ZE02
> detrás. La pista definitiva es que `SendNominaSync` no recibe `testSetId`
> (solo `contentFile`), así que el `TestSetId` que la DIAN entrega para nómina
> no tendría dónde viajar.

## 9 bis. El rechazo ZE02 — causa raíz y cómo no repetirlo

Durante la habilitación la DIAN rechazó **quince** nóminas seguidas con
**ZE02, "Valor de la firma inválido"** (código 99), mientras todas las reglas
NIE pasaban. Costó dos días y quince envíos del cupo del Set de Pruebas. Esto es
lo que era, cómo se encontró y qué hay que respetar para que no vuelva.

### El síntoma engaña

`ZE02` dice "firma inválida", pero **la firma nunca estuvo mal**. En todas las
nóminas rechazadas:

- los tres digests (`documento`, `KeyInfo`, `SignedProperties`) recalculaban exactos;
- el `SignatureValue` verificaba contra el certificado incrustado;
- la estructura XAdES era idéntica, elemento a elemento, a la de un documento
  que la DIAN sí había aceptado;
- el XML validaba contra `NominaIndividualElectronicaXSDV1.0.6.xsd`;
- los totales cuadraban con los conceptos.

Por eso la investigación se fue durante dos días detrás de la criptografía y de
los datos, que era donde no estaba.

### La causa raíz

**El documento tiene que reproducir el orden de declaraciones y atributos de la
ejemplificación oficial de la DIAN.** Concretamente, en el elemento raíz:

```xml
<NominaIndividual xmlns="dian:gov:co:facturaelectronica:NominaIndividual"
                  xmlns:xs="http://www.w3.org/2001/XMLSchema-instance"
                  xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
                  xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
                  xmlns:xades="http://uri.etsi.org/01903/v1.3.2#"
                  xmlns:xades141="http://uri.etsi.org/01903/v1.4.1#"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  SchemaLocation=""
                  xsi:schemaLocation="…">
```

El namespace por defecto **primero**, después `xs`, `ds`, `ext`, `xades`,
`xades141`, `xsi`, y `SchemaLocation=""` **antes** de `xsi:schemaLocation`.
Emitíamos el mismo conjunto de declaraciones en otro orden, y eso bastaba para
el rechazo.

Esto **no debería importar**. La C14N ordena declaraciones y atributos antes de
digerir, así que el orden no cambia ningún digest —lo comprobamos: el digest del
documento es byte a byte el mismo antes y después de reordenar— y cualquier
validador conforme aceptaría las dos formas. El validador de nómina de la DIAN
no lo hace. No hay explicación pública; lo que hay es la evidencia de abajo.

### La evidencia

Quince rechazos y tres aceptaciones, con el contenido controlado:

| Documento | Contenido | Generado por | Orden de la raíz | Resultado |
|---|---|---|---|---|
| NE1000–NE1012 | variado | nobelio | propio | ZE02 ×13 |
| NESETP1, NESETP2 | mínimo | Platino (PHP) | el del anexo | **aceptado** |
| NESETP3 | rico (transporte, hora extra) | nobelio | propio | ZE02 |
| NESETP4 | mínimo, empleado real | nobelio | propio | ZE02 |
| NESETP5 | **idéntico a NESETP2** | nobelio | propio | ZE02 |
| NESETP6 | **idéntico a NESETP5** | nobelio | **el del anexo** | **aceptado** |

Las dos filas que cierran el caso son las dos últimas: NESETP5 tenía exactamente
los mismos datos que una nómina aceptada y se rechazó, lo que descarta el
contenido; NESETP6 tenía exactamente los mismos datos que NESETP5 y se aceptó,
con el orden de la raíz como único cambio del documento.

### Lo que está confirmado y lo que no

Entre NESETP5 y NESETP6 se cambiaron **dos cosas a la vez**, así que lo
confirmado es la combinación, no cada parte por separado:

1. El orden de declaraciones y atributos de la raíz (arriba).
2. `xades:QualifyingProperties` declara `xmlns:xades` y `xmlns:xades141`.

Y arrastramos una tercera de antes, tampoco aislada:

3. `ds:Signature` declara **todas** las declaraciones que heredaba de la raíz.

Las tres las cumplen los documentos que la DIAN acepta. **No se sabe cuáles son
individualmente necesarias**, y aislarlo cuesta envíos del cupo, así que se
dejan las tres. Si alguna vez hay que tocar una, hay que saber que se está
moviendo algo que la DIAN mira.

Todas son inocuas para la firma: ninguna cambia el conjunto de namespaces en
ámbito ni el orden canónico, así que ningún digest se altera. Lo comprueba
`FirmaAisladaTests`.

### Cómo se implementa

- **La raíz se parsea de una plantilla** (`ConstructorNominaXML._raiz`), no se
  construye con `nsmap`. Por el árbol es imposible: lxml emite el `nsmap` en el
  orden del diccionario y, al pedirle un atributo por URI, reutiliza el primer
  prefijo declarado para esa URI —lo que con `xs` delante convierte
  `xsi:schemaLocation` en `xs:schemaLocation`—.
- **Las declaraciones de la firma se escriben sobre los bytes**
  (`FirmadorXAdES._declarar_contexto_heredado`), no con `etree`: lxml descarta
  en silencio una declaración que un ancestro ya trae idéntica. Va después de
  firmar, y es seguro porque queda fuera de todo lo canonicalizado.

### Cómo diagnosticar un ZE02 futuro

En este orden, que es el barato primero:

1. **Verificar la firma en local**, incluida la comprobación **aislada** (la
   firma extraída de su documento): `FirmaAisladaTests`. Si falla ahí, es la
   firma de verdad y no hace falta gastar un envío.
2. **Validar contra el XSD** de `apps/dian/datos/xsd/nomina/`.
3. **Diffear el XML contra uno aceptado**, normalizando lo que cambia
   legítimamente (CUNE, digests, `SignatureValue`, UUID de la firma,
   `SigningTime`, fechas). Cualquier diferencia que quede —aunque sea de orden
   de atributos o de declaraciones— es sospechosa. **Ahí estaba esta.**
4. Solo entonces mirar los datos.

Y la regla de oro que nos habría ahorrado dos días: **no cambiar dos variables a
la vez**. Las dos primeras aceptadas venían de otra implementación *y* con
contenido más simple, y esa confusión hizo dar por resuelto el caso antes de
tiempo.

> **Confirmado el 2026-08-30** con NESETP6: "Procesado Correctamente", código 00.

> Esto retiró `apps/dian/variantes_firma.py`, el módulo temporal que servía para
> mandar una variante por documento e ir acorralando el rechazo, junto con el
> parámetro `variante` del endpoint `emitir` y del servicio.

## 10. Qué NO aplica

Resolución de numeración, `sts:DianExtensions`, `InvoiceControl`, clave técnica,
`AttachedDocument`, adquiriente, impuestos (`TaxTotal`), y toda la maquinaria de
CUFE/CUDE/CUDS. Sí aplican, sin cambios: la firma XAdES con su política, el
certificado, el cliente SOAP y el manejo de la respuesta.

---

### Casos de prueba derivados

1. **XSD**: el `NominaIndividual` y el `NominaIndividualDeAjuste` que emitamos
   validan contra `apps/dian/datos/xsd/nomina/`. Las ejemplificaciones oficiales
   fallan por una sola razón esperable: traen `<ext:UBLExtensions>` vacío, que
   es también el estado en que `ConstructorNominaXML` deja el XML antes de
   firmarlo. **Comprobado** el 2026-08-27: firmado con el certificado real, el
   `NominaIndividual` valida.
2. **SoftwareSC**: el mismo cálculo que la factura, ya cubierto.
3. **CUNE**: no hay vector de prueba oficial válido (ver §3).
4. **Firma**: además de verificarla en su documento, hay que verificarla
   **extraída** de él (`FirmaAisladaTests`). Es la comprobación que distingue un
   ZE02 por cálculo mal hecho de uno por representación; ver §9 bis.
