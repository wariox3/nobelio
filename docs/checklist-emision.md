# Checklist: del alta a la emisión de un documento

Pasos en orden de dependencia para dejar un emisor listo y emitir su primer
documento electrónico ante la DIAN. Cada paso indica el endpoint y lo que no
puede faltar. Las rutas cuelgan de `/api/`.

> Autenticación: el **frontend** usa JWT (`Authorization: Bearer <access>`); el
> **ERP** usa API Key (`Authorization: Api-Key <prefijo>.<secreto>`).
> Ver [docs/autenticacion.md](autenticacion.md).
>
> Errores: todas las respuestas 4xx/5xx tienen el mismo cuerpo
> `{"detail": "<mensaje>", "errores": {"<campo>": ["<msg>"]}}` (`errores` queda
> `{}` cuando el error no es por campo). Lo normaliza `apps.nucleo.api.exception_handler`.

---

## 0. Prerrequisitos del sistema (una sola vez)

- [ ] **Catálogos DIAN cargados**: `python manage.py cargar_catalogos`
      (tipos de identificación, organización, tributos, países, municipios, …).
- [ ] **Backblaze B2 configurado** (`B2_*` en `.env`): obligatorio para subir
      certificados; el `.p12` se guarda siempre en B2, nunca en disco.
- [ ] **Ambiente DIAN** definido (`DIAN_ENVIRONMENT`: `2` habilitación / `1` producción).
- [ ] **Usuario staff** inicial para poder crear cuentas y usuarios.

## 1. Cuenta (tenant)

- [ ] `POST /api/cuentas/cuenta/` → crea la cuenta que agrupa usuarios y emisores.

## 2. Usuario y acceso

- [ ] `POST /api/seguridad/usuario/` (solo staff) → usuario por **email**, ligado a la cuenta.
- [ ] `POST /api/seguridad/token/` con email + contraseña → obtiene `access`/`refresh` (JWT).
- [ ] *(ERP, opcional)* `POST /api/seguridad/llave-api/` → credencial API Key para el ERP.

## 3. Emisor (OFE)

- [ ] *(opcional)* `GET /api/emisores/emisor/validar-nit/?nit=<NIT>` → consulta el RUES
      y autocompleta datos. El alta **no** lo exige: es ayuda para el formulario.
- [ ] `POST /api/emisores/emisor/` → crea el emisor ligado a la **cuenta**
      (razón social, NIT + DV, tipo de organización, ubicación, responsabilidades).

## 4. Certificado digital

Va **antes** que el software: es lo que firma la consulta de numeración y los
documentos, así que sin él los pasos 5 a 7 no arrancan. Registrar un software
sin certificado activo y vigente responde 400.

- [ ] `POST /api/emisores/certificado/cargar/` (multipart: `emisor`, `archivo` .p12/.pfx, `clave`).
  - Se **valida** antes de guardar: integridad + clave, llave RSA ≥ 2048, vigencia,
    y que el **NIT del certificado coincida** con el del emisor.
  - `vigente_desde`/`vigente_hasta` se autocompletan del propio certificado.
  - Se guarda en **B2** (`<id_emisor>/certificados/`); un único certificado **activo** por emisor
    (cargar uno nuevo jubila el anterior).

## 5. Software DIAN

- [ ] `POST /api/emisores/emisor/crear-habilitacion/` con `emisor`, `identificador`
      y `pin` (más `test_set_id` si se va a correr el Set de Pruebas) → registra el
      software del emisor y jubila el que tuviera activo.
  - Antes de registrar nada comprueba que el emisor exista y que tenga un
    **certificado activo y no vencido** (paso 4). Si no, responde 400.
  - Los tres datos los entrega la DIAN al aprobar el software. El `test_set_id`
    es opcional en el modelo —un software ya en producción no tiene set de
    pruebas—, pero sin él no se puede emitir el Set de Pruebas después.
  - Responde **200**. Un solo software activo por emisor: registrar otro deja el
    anterior en `activo=False`, como histórico.
  - El `ProviderID` del XML **no se registra**: en software propio el proveedor
    tecnológico es el propio emisor, así que sale de su NIT (y el `schemeID`, de
    su dígito de verificación).
- [ ] *(equivalente)* `POST /api/emisores/software/` registra el software por su
      propio endpoint CRUD.

> **El Set de Pruebas no está automatizado.** No hay un endpoint que lo corra de
> principio a fin: los documentos de habilitación se crean y se envían a mano con
> los endpoints de documentos (pasos 7 y 8), que es lo que hace falta para
> habilitarse. Mientras `SoftwareDian.set_pruebas_aceptado` sea `False` y
> `DIAN_ENVIRONMENT` sea `2`, los envíos van por `SendTestSetAsync` con el
> `test_set_id` del software; hay que ponerlo a `True` a mano cuando la DIAN
> acepte el set, para que pase a `SendBillSync`. Lo mismo con
> `Emisor.habilitado_facturacion`: hoy nada lo marca solo.

## 6. Resolución de facturación

Hay dos vías. La recomendada es traer los datos directamente de la DIAN
(incluida la **clave técnica**, que no se puede cargar manualmente por la API):

- [ ] `GET /api/emisores/resolucion/consulta-dian/?emisor=<id>` → consulta
      `GetNumberingRange` y **previsualiza** los rangos autorizados (sin guardar;
      la clave técnica no se expone, solo se indica si está presente).
      El parámetro `emisor` es obligatorio.
- [ ] `POST /api/emisores/resolucion/importar-dian/` con
      `{"emisor": <id>, "tipo_factura": <id>}` → consulta `GetNumberingRange` y
      crea/actualiza las resoluciones, guardando la `clave_tecnica` en el servidor.
      Requiere certificado y software DIAN activos del emisor.

- [ ] *(alternativa manual)* `POST /api/emisores/resolucion/` → número y fecha de
      resolución, `prefijo`, `rango_desde`/`rango_hasta`, vigencias y `tipo_factura`.
      El `consecutivo_actual` avanza con cada emisión.

## 7. Documento electrónico

- [ ] `POST /api/documentos/documento/` (`DocumentoCrearSerializer`):
      `documento_tipo`, `emisor`, `numero_resolucion`, `adquiriente`, `moneda`,
      formas/medios de pago, y `detalles` (cada uno con sus `impuestos`/tributos).
      Los totales se calculan al crear.
- La resolución se pide **siempre** por `numero_resolucion` (el número DIAN, que
  es lo que el emisor conoce); el id no se acepta al crear. Se busca entre las
  **activas** del emisor del documento; si el número está repetido (importado
  para varios tipos de factura o prefijos) desempata el `prefijo` del documento,
  y si aun así no se puede decidir responde 400. En la lectura vuelven
  `resolucion` (id) y `resolucion_numero`. En un `PATCH` no hay que repetirlo:
  se usa la resolución que el documento ya tiene.
- El `adquiriente` va **anidado en el documento**, no por id: no hay cartera de
  clientes ni endpoint propio. Cada documento guarda su copia del receptor (1:1,
  `documento.adquiriente`), que es la que quedó firmada en el XML y entró en el
  CUFE; así, corregir un dato del cliente en una factura nueva no reescribe las
  ya emitidas. Se puede corregir el de un borrador con `PATCH` sobre el
  documento, y al borrar el documento se borra con él.
- Un documento **firmado o posterior no se puede modificar** (400): sus datos ya
  viajaron en el XML y en el CUFE, y cambiarlos solo lograría que el PDF dejara
  de coincidir con lo firmado.
- Se rechaza (400 en `prefijo` o `consecutivo`) si el número del documento no cabe
  en lo que autorizó la resolución: el `prefijo` tiene que ser el de la resolución
  y el `consecutivo` estar entre `rango_desde` y `rango_hasta` (extremos incluidos).
  Sin esto el documento se crea, se firma —consumiendo consecutivo— y la DIAN lo
  rechaza al enviarlo. También se comprueba al modificarlo con `PATCH`.
- Se rechaza (400 en `emisor`) si el emisor **no está en condiciones de firmar**:
  inactivo, sin certificado activo, o con el certificado vencido o aún sin regir.
  Es la misma regla que aplica `emitir/` (`emisores.servicios.motivo_no_puede_emitir`),
  comprobada ya al crear para no dejar borradores que nunca se van a poder emitir.

## 8. Ciclo de vida DIAN

- [ ] `POST /api/documentos/documento/{id}/emitir/` → genera XML UBL 2.1, calcula
      **CUFE/CUDE** y **firma XAdES-EPES** (requiere certificado activo del emisor).
- [ ] `POST /api/documentos/documento/{id}/enviar/` → envía a la DIAN por WS;
      devuelve `track_id`, `es_valido`, `codigo_estado` y errores.
      Se usa `SendTestSetAsync` (con el `test_set_id`) solo mientras se está en
      habilitación **y** el Set de Pruebas aún no ha sido aceptado; una vez
      aceptado (`SoftwareDian.set_pruebas_aceptado`) o en producción, `SendBillSync`.
      El documento queda en `aceptado`, `rechazado` o —si la DIAN no ha resuelto
      todavía— `enviado`.
- [ ] *(si quedó en `enviado`)* `GET /api/documentos/documento/{id}/consultar/` →
      pregunta a la DIAN **sin tocar** el documento; devuelve `es_valido`,
      `codigo_estado` y errores.
- [ ] *(si quedó en `enviado`)* `POST /api/documentos/documento/{id}/actualizar-estado/` →
      consulta y **aplica** el resultado. Solo para documentos enviados o
      rechazados: sobre un borrador o uno ya aceptado responde 400.
- [ ] `GET /api/documentos/documento/{id}/xml/` → descarga el XML firmado.
- [ ] `GET /api/documentos/documento/{id}/pdf/` → descarga la representación gráfica (PDF con QR).

---

### Resumen de dependencias

```
Cuenta → Llave API / Usuario
      └→ Emisor → Certificado (B2)
                    └→ crear-habilitacion/ → Software DIAN
                → Resolución
                → Documento (lleva dentro al adquiriente)
                       └→ emitir → enviar → xml/pdf
                                      │
                                      └→ (enviado) consultar /
                                                   actualizar-estado
```

Estados del documento (`DocumentoEstado.Nombre`):
`borrador → firmado → enviado | aceptado | rechazado`.
