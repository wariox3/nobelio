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
├── FechasPagos      FechaPago+
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
  `EliminandoPredecesor` con los mismos tres datos.

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
que ya usa la factura.

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

- **`SendNominaSync`**: síncrona, un solo documento por ZIP. No hay
  `SendTestSetAsync` para nómina: la habilitación se hace contra este mismo
  método.
- **`GetStatus`**: se amplía para consultar documentos de nómina.

## 10. Qué NO aplica

Resolución de numeración, `sts:DianExtensions`, `InvoiceControl`, clave técnica,
`AttachedDocument`, adquiriente, impuestos (`TaxTotal`), y toda la maquinaria de
CUFE/CUDE/CUDS. Sí aplican, sin cambios: la firma XAdES con su política, el
certificado, el cliente SOAP y el manejo de la respuesta.

---

### Casos de prueba derivados

1. **XSD**: el `NominaIndividual` y el `NominaIndividualDeAjuste` que emitamos
   validan contra `apps/dian/datos/xsd/nomina/`. Las ejemplificaciones oficiales
   fallan por una sola razón esperable: traen `<ext:UBLExtensions>` vacío.
2. **SoftwareSC**: el mismo cálculo que la factura, ya cubierto.
3. **CUNE**: no hay vector de prueba oficial válido (ver §3).
