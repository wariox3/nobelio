# Recepción de facturas de proveedores

Estado: **en construcción, por pasos**. Iniciado el 2026-10-02. Hoy los correos
llegan, se registran y se procesan: cabeceras, adjuntos y documentos.

Nobelio recibe por correo las facturas que los proveedores les mandan a los
emisores registrados, extrae los documentos electrónicos (factura, nota crédito,
nota débito) y los deja disponibles en nobelio, desde donde el emisor los
consulta y les registra los eventos RADIAN. El proceso de recibo existe solo
aquí: no se le entrega nada al ERP. Nobelio no es multitenant: la
"empresa" es el `Emisor`, y el acceso se resuelve con el alcance de siempre
(`apps/seguridad/alcance.py`).

## Flujo

```
proveedor ──► compras@cliente.com ──(reenvío)──► <nit>@recepcion.rededoc.uk
                                                         │
                                       Cloudflare Email Routing (catch-all)
                                                         │
                                          Email Worker `nobelio-recepcion`
                                           1. guarda el MIME en R2
                                           2. POST /recepcion/inbound
                                                         │
                                         nobelio: registra `rec_correo`
                                                         │
                          Celery (cola `recepcion`): `procesar_correo`
```

| Ambiente | Dominio del buzón | nobelio |
|---|---|---|
| Pruebas | `<nit>@recepcion.rededoc.uk` | `https://api.rededoc.uk` |
| Producción | `<nit>@recepcion.rededoc.co` | `https://api.rededoc.co` |

### Lo que manda el Worker

`POST /recepcion/inbound`, fuera de `/api/` y sin barra final (`APPEND_SLASH`
no puede redirigir un POST):

| Cabecera | Contenido |
|---|---|
| `Content-Type` | `message/rfc822` |
| `Authorization` | `Bearer <INBOUND_TOKEN>` |
| `X-Envelope-To` | destinatario, p. ej. `901192048@recepcion.rededoc.uk` |
| `X-Envelope-From` | remitente del sobre |
| `X-Raw-Key` | clave del objeto en R2: `AAAA-MM-DD/<uuid>.eml` |

El body es el MIME crudo. Este Bearer es solo de esta ruta: no tiene nada que
ver con la autenticación de la API (`docs/autenticacion.md`).

## Estado

### Hecho

- **App `apps.recepcion`** con el modelo `Correo` (`rec_correo`):
  alias, sha256 (único), raw_key, envelope_from/to, message_id, asunto,
  recibido_en, estado, error_detalle, intentos, confirmacion_reenvio y emisor
  (FK nullable, `PROTECT`). Migración `0001_initial`.
- **Endpoint `POST /recepcion/inbound`** (`apps/recepcion/views/inbound.py`):
  - Valida el Bearer contra `INBOUND_TOKEN` con `hmac.compare_digest`. Si la
    variable está vacía rechaza todo (falla cerrado).
  - Lee el body por stream con un tope de 30 MB (413), sin pasar por
    `DATA_UPLOAD_MAX_MEMORY_SIZE`.
  - Idempotente por el SHA-256 del body: 201 si es nuevo, 200 si ya existía.
  - Guarda el alias (parte local de `X-Envelope-To`, en minúsculas) y, si es
    el NIT de un emisor, lo asocia (`Emisor.numero_identificacion`, sin DV).
    Si no corresponde a ninguno, el correo queda sin emisor. No abre el MIME
    ni sus adjuntos, y no encola nada.
- **Tests**: `apps/recepcion/tests_inbound.py`, 14 casos.
- **API de consulta `GET /api/recepcion/correo/`** (`apps/recepcion/views/correo.py`),
  de solo lectura y acotada con `AlcanceEmisorMixin`: cada usuario ve los
  correos de sus emisores, y el staff todos, incluidos los que no tienen emisor.
  - Filtros: `?emisor=<id>`, `?estado=<estado>`, `?desde=AAAA-MM-DD` y
    `?hasta=AAAA-MM-DD` (sobre `recibido_en`, en hora de Colombia, inclusive).
  - `?search=` en remitente, asunto y Message-ID; `?ordering=recibido_en|estado`.
  - Expone `raw_key` (la clave del MIME en R2) pero no `sha256`. Tests en `apps/recepcion/tests_api.py`.
  - `DELETE /api/recepcion/correo/<id>/` elimina un correo **sin emisor** (el de
    una empresa que no está ni va a estar en la plataforma): la fila, sus
    adjuntos en B2 y su MIME en R2 (`apps/recepcion/r2.py`), o nada. Un correo con emisor
    responde 400; sin las variables `R2_*`, 503, y si R2 falla, 502 y la fila
    se queda. Como los correos sin emisor solo los ve el staff o una llave de
    alcance global, en la práctica solo ellos pueden eliminarlos.
  - `DELETE /api/recepcion/correo/<id>/eliminar-admin/` fuerza la eliminación
    de **cualquier** correo: sus documentos (de cualquier emisor), todos sus
    adjuntos en B2 y el MIME en R2. Solo el staff o una llave de alcance global (permiso
    `AlcanceTotal` de `apps/seguridad/alcance.py`); el resto, 403. Responde 200
    con `{correo, documentos, archivos}`. Filas en una transacción y archivos
    dentro de ella: si B2 o R2 fallan, las filas vuelven (502) y repetir la
    petición termina el trabajo. Sin `R2_*`, 503 sin tocar nada.
- **Procesamiento en Celery** (`apps/recepcion/tareas.py`, cola `recepcion`):
  - El endpoint encola `procesar_correo` al registrar el correo, y también al
    recibir un repetido que sigue `pendiente` (por si el broker falló la
    primera vez).
  - `apps/recepcion/procesamiento.py` descarga el MIME de R2 (`r2.py`) y
    guarda asunto y `Message-ID` decodificados. Si el remitente es
    `forwarding-noreply@google.com`, el estado es `confirmacion_reenvio` y en
    `confirmacion_reenvio` quedan el código y el enlace; si no, `procesado`.
  - Solo procesa correos `pendiente` o `error`: puede correr dos veces sin
    efecto doble, y un correo en error se reprocesa reencolándolo.
  - Fallos transitorios de R2 (red, 5xx): 5 reintentos a 1, 2, 4, 8 y 16
    minutos. Permanentes (sin el MIME, credenciales rechazadas, sin `R2_*`):
    `error` en el acto. Cada intento suma a `intentos`.
  - En desarrollo también lee de R2, con las mismas variables.
- **Documentos** (`apps/recepcion/extraccion.py` y `procesamiento.py`):
  - Recorre XML y PDF adjuntos, ZIP (también anidados) y correos adjuntos
    (`message/rfc822` o `.eml`). Un `.docx`/`.xlsx` no se abre como ZIP.
  - Lee el AttachedDocument (documento del CDATA de
    `cac:Attachment/cac:ExternalReference/cbc:Description` y validación de
    `ResultOfVerification`) o un Invoice, CreditNote o DebitNote suelto.
    Invoice con 01–04 es factura de venta; otro código (05, documento
    soporte) se ignora.
  - Seguridad: XML sin DTD, entidades ni red (no hay XXE), y topes de 4 niveles
    de anidamiento, 200 archivos y 100 MB descomprimidos; pasarlos deja el
    correo en `error`.
  - El PDF va con el XML de su mismo nombre en el mismo contenedor; si hay un
    solo documento y un solo PDF, van juntos aunque estén en sitios distintos.
  - Modelo `Documento` (`rec_documento`), aparte de `doc_documento` (allá el
    emisor factura; aquí recibe). Reusa `DocumentoTipo` y `Moneda`. No tiene
    archivos propios: son `Adjunto` con `documento` apuntándole.
- **Adjuntos** (`rec_adjunto`, `apps/recepcion/adjuntos.py`): **todos** los
  archivos del correo, ya fuera de sus ZIP (los ZIP no se guardan; su
  contenido sí, y un ZIP dañado se guarda tal cual).
  - Campos: `correo` (siempre), `documento` (si es de uno), `rol`
    (`xml`, `xml_documento`, `pdf`, `otro`), `archivo` en B2, `nombre`
    original (sin rutas), `tipo_contenido`, `tamano`, `sha256`, `creado_en`.
  - La base de datos garantiza un solo archivo por rol y documento, y que un
    adjunto sin documento sea `otro`.
  - En B2: `<emisor del documento, o del correo, o sin-emisor>/recepcion/<aaaa>/<mm>/<uuid>.<ext>`.
    El nombre del proveedor nunca llega al bucket.
  - Los archivos de un documento repetido o de un receptor desconocido quedan
    como `otro`. No se deduplica por hash.
  - El registro de documentos y adjuntos es todo o nada: si B2 falla a mitad,
    se deshace y se borra lo que alcanzó a subir; el reintento no duplica.
  - `PROTECT` hacia correo y documento: se borran solo con
    `adjuntos.eliminar_correo`, que borra también B2 y R2.
  - API: `GET /api/recepcion/adjunto/` (filtros `?correo`, `?documento`,
    `?rol`, `?search` en el nombre), su detalle y `descargar/` (siempre como
    descarga, con el nombre original). `GET /api/recepcion/correo/<id>/adjuntos/`
    los lista por correo. Cada quien ve los del documento si alcanza su emisor,
    y los demás si alcanza el del correo.
  - El emisor del documento es el del **NIT receptor del XML**, aunque el
    correo llegara a otro buzón. Si el correo no tenía emisor, toma ese.
  - CUFE único: un repetido se ignora (sin fila ni archivos).
  - Estado del correo: `procesado` (al menos un documento de un emisor, nuevo o
    repetido), `sin_documentos` o `empresa_desconocida` (ninguno es de un
    emisor; no se guarda nada).
  - En `GET /api/recepcion/correo/` cada correo trae `documentos`, solo los de
    los emisores que alcanza quien consulta.
  - No se verifica la firma ni el CUFE de los documentos.
- **API de documentos `GET /api/recepcion/documento/`**
  (`apps/recepcion/views/documento.py`), de solo lectura y acotada con
  `AlcanceEmisorMixin`. `basename` propio (`documento-recibido`) para no chocar
  con `documento-detail` de la emisión.
  - Filtros: `?emisor`, `?correo`, `?documento_tipo`, `?proveedor` (NIT sin DV)
    y `?desde`/`?hasta` sobre `fecha_emision`.
    `?ordering=fecha_emision|numero|total_a_pagar|creado_en`.
  - `?search=`: CUFE/CUDE completo y exacto, NIT del proveedor por el
    comienzo, y número y razón social en cualquier parte.
  - Paginación (2026-10-08, es el listado de más consumo): 25 por defecto,
    `?page_size=` hasta 100 (`PaginacionAjustable`). El orden siempre
    desempata por `id` (`OrdenEstable`), así que con OFFSET ninguna fila se
    repite ni se pierde entre páginas.
  - Índices: `(emisor, -fecha_emision, -creado_en, -id)` cubre la bandeja en
    orden y sin ordenar aparte. La búsqueda va con `UPPER(cufe_cude)` y con
    trigramas (`pg_trgm`, GIN) sobre `UPPER()` de NIT, número y razón social.
    `tiene_pdf` y `tiene_xml_factura` salen con `EXISTS` en la misma consulta,
    sin cargar los adjuntos.
  - Recomendación para el front: mandar siempre `emisor` y un rango de fechas
    por defecto (el mes en curso).
  - Descargas: `xml/` (como llegó), `xml-factura/` (el documento sin el
    AttachedDocument; si llegó suelto, el mismo de `xml/`) y `pdf/` (400 si el
    proveedor no lo mandó). `tiene_pdf` y `tiene_xml_factura` lo anticipan.
- **Comando `reprocesar_correos`** (`apps/recepcion/management/commands/`):
  `--id <n>` o `--todos`. Corre en el mismo proceso, no en Celery, y muestra
  cómo quedó cada correo.
  - Reprocesa los correos en `error`, en `pendiente` y en
    `empresa_desconocida` (`procesamiento.reprocesar`). Con `--todos` se
    salta los pendientes de menos de 30 minutos, que pueden seguir en la cola.
  - Antes borra los adjuntos del intento anterior, de la base y de B2
    (`adjuntos.vaciar_correo`), para no duplicarlos. Un correo con documentos
    no se reprocesa.
  - Si el correo sigue sin emisor, lo busca otra vez por el alias
    (`procesamiento.emisor_del_alias`, el mismo del endpoint).
  - Si R2 o B2 fallan, el correo queda `pendiente` y sin adjuntos, listo para
    otro intento. Tests en `apps/recepcion/tests_reprocesar.py`.
- **nginx**: bloque `location = /recepcion/inbound` con `client_max_body_size
  30M` (`docs/despliegue.md`).
- **Cloudflare (pruebas, `rededoc.uk`)**, funcionando de punta a punta:
  - Email Routing activo en `rededoc.uk`, con el subdominio `recepcion`.
  - Bucket R2 `nobelio-inbound-raw`.
  - Worker `nobelio-recepcion` con la vinculación R2 `RAW` y la variable
    `NOBELIO_URL = https://api.rededoc.uk/recepcion/inbound`.
  - Regla catch-all: enviar al Worker `nobelio-recepcion`.
  - Secreto `INBOUND_TOKEN` en el Worker, el mismo valor en `/opt/nobelio/.env`.
    Desplegado y verificado el 2026-10-03: los correos entran con `201`.

## Siguientes pasos

En orden. Cada uno se cierra (código, tests, despliegue en pruebas) antes de
empezar el siguiente.

1. **Producción**: repetir lo de Cloudflare en `rededoc.co` y apuntar el Worker
   a `api.rededoc.co`.

Después siguen los eventos RADIAN (sección siguiente, pasos R1 a R5).

## Eventos RADIAN (análisis, 2026-10-06)

Fuente: **Anexo Técnico RADIAN 1.1** (Resolución 000085 de 2022), resumido en
[anexo-radian.md](anexo-radian.md). Los ejemplos oficiales del 030 al 034
están en `apps/dian/datos/ejemplos/radian/` y validan contra el XSD que ya
teníamos. Lo marcado con ⚠ sigue abierto.

### Qué son

Un evento es un `ApplicationResponse` UBL 2.1 firmado que un participante le
transmite a la DIAN sobre una factura electrónica de venta ya validada. La DIAN
lo valida, lo registra en RADIAN y queda ligado al CUFE.

**Alcance: solo los eventos que el emisor emite como adquiriente**, sobre las
facturas que recibe de sus proveedores (`rec_documento`), desde el
frontend de nobelio: el proceso de recibo existe solo aquí, el ERP no
interviene. Los eventos sobre las facturas que el emisor emite **quedan fuera**:
enterarse de los eventos de sus clientes, la aceptación tácita (034) y el
bloqueo de notas sobre facturas aceptadas (decidido 2026-10-06).

| Código | Evento | Lo emite | Se lo dirige a | Plazo (regla DIAN) |
|---|---|---|---|---|
| 030 | Acuse de recibo | Adquiriente | Facturador | No antes de la fecha de la factura (DC24a) |
| 032 | Recibo del bien o prestación del servicio | Adquiriente | Facturador | No antes del 030 (DC24b) |
| 033 | Aceptación expresa | Adquiriente | Facturador | Dentro de 3 días hábiles del 032 (DC24c) |
| 031 | Reclamo | Adquiriente | Facturador | Excluye al 033. Plazo: 3 días hábiles por el Código de Comercio; el anexo RADIAN no trae regla propia ⚠ |

El 034 (aceptación tácita) lo emite el facturador, así que lo emite el proveedor
y no nos toca.

- El 030 y el 032 llevan la **persona** que recibe (documento, nombres, cargo
  y área). El 031 lleva el concepto (`Concepto de Reclamo.gc`) en
  `cbc:ResponseCode/@listID`.
- La DIAN rechaza un evento repetido y uno incoherente con los anteriores.
- Con aceptación (033 o 034), el proveedor ya no puede emitir notas crédito ni
  débito sobre esa factura.
- El anexo dice que estos eventos son para facturas **a crédito** que se
  quieren usar como título valor. Los eventos de título valor (035–051) **quedan
  fuera de alcance**.
- Las reglas campo a campo de los eventos (`AAxx`) no están en este anexo sino
  en el **Anexo Técnico FE 1.9**, que no tenemos ⚠. Con los ejemplos y el
  encabezado común alcanza para construirlos; los rechazos en habilitación
  dirán lo que falte.

### Técnica (confirmada en el anexo)

- **XML**: el encabezado es `CustomizationID` 1,
  `ProfileID` «DIAN 2.1: ApplicationResponse de la Factura Electrónica de
  Venta», `ProfileExecutionID` y `UUID/@schemeID` según el ambiente. El
  `cbc:ID` es un consecutivo propio (sin resolución) que no se repite por tipo
  de evento. Después vienen `SenderParty`, `ReceiverParty` y
  `DocumentResponse`, con la referencia a la factura (número, CUFE y tipo 01).
  `DianExtensions` como en la factura, pero sin `InvoiceControl`. Firma
  XAdES-EPES de siempre.
- **CUDE**: `SHA-384(Num_DE + Fec_Emi + Hor_Emi + NitFE + DocAdq + ResponseCode
  + ID factura + DocumentTypeCode + PIN)`. El vector del anexo está comprobado
  (en `anexo-radian.md`). El `SoftwareSecurityCode` es el de la factura.
- **WS**: el mismo endpoint y el mismo sobre que la factura.
  `SendEventUpdateStatus(contentFile)` es síncrono, recibe un ZIP con un solo
  `ApplicationResponse` y responde como `SendBillSync`.
  `GetStatusEvent(trackId=CUFE)` devuelve los eventos de la factura: sirve
  para conciliar lo que tenemos con lo que registró la DIAN.
- **Entrega a la contraparte**: por correo, con el asunto
  `Evento;<factura>;<NIT>;<nombre>;<número evento>;<código>;<línea opcional>`
  y un ZIP de máximo 2 MB con el `AttachedDocument` (el evento y la respuesta
  de la DIAN). Sale por Zinc, como las facturas.
- **Software**: se envía con el `SoftwareDian` de facturación del emisor. Hay
  que confirmar si la DIAN exige una habilitación aparte para eventos ⚠: se
  sabrá al enviar el primero en habilitación (R3).

### Lo que hay y lo que falta

| Pieza | Estado |
|---|---|
| Firma XAdES, sobre WS-Security, ZIP, `SoftwareSecurityCode` | Hay. Se reusan |
| XSD de `ApplicationResponse` y ejemplos 030–033 | Hay |
| CUDE del evento | Falta. Composición y vector confirmados |
| `AttachedDocument` | Hay el de las facturas. Hay que adaptarlo al evento |
| Catálogo de eventos | Hecho (R1): `EventoRadian`, transcrito de los ejemplos, porque ninguna lista `.gc` trae los 030–033 vigentes |
| Conceptos de reclamo | Hecho (R1): `ConceptoReclamo` |
| `SendEventUpdateStatus` / `GetStatusEvent` | Faltan |
| Días hábiles con festivos de Colombia | Hecho (R1): catálogo `Festivo` y `calendario.py` |

### Pasos

Van después del punto 1 de arriba, con la misma regla: cada paso se cierra
antes de empezar el siguiente.

- **R1. Catálogos** ✅ (2026-10-06): `EventoRadian` (030–033, id = código),
  `ConceptoReclamo` (lista oficial, id = código) y `Festivo` (propio: código =
  fecha, id = `AAAAMMDD`, 2026 y 2027) en `apps/catalogos`, cargados por
  `cargar_catalogos` (migración `0002`). Los dos primeros se publican en
  `/api/catalogos/evento-radian/` y `/api/catalogos/concepto-reclamo/`.
  `apps/catalogos/calendario.py` tiene `es_habil(fecha)` y
  `sumar_dias_habiles(desde, n)`. Si falta el año de festivos que un plazo
  recorre, lanza `FestivosNoCargados` en vez de contar mal. **Mantenimiento:**
  cargar los festivos del año siguiente antes del 1 de enero
  (`datos/listas/calendario/README.md`).
- **R2. Generación** ✅ (2026-10-06): `ConstructorEvento` en
  `apps/dian/ubl/evento.py`. No parte de un modelo: recibe un `Evento` con sus
  `Parte` (quien lo emite y el proveedor) y la `Persona` que recibe (030 y
  032). Valida que el código sea 030–033, que el 030 y el 032 traigan la
  persona y que el 031 traiga el concepto.
  - `calcular_cude_evento` en `identificadores.py`, con el vector del anexo.
  - Se firma con el `FirmadorXAdES` de siempre y valida contra el XSD.
  - La estructura es la del ejemplo oficial 030, elemento por elemento, salvo
    una diferencia: el DV (`@schemeID`) solo se emite en identificaciones NIT,
    no en cédulas. Los ejemplos ponen `4` de relleno en casi todas, también en
    NIT donde no cuadra. ⚠ Confirmarlo en habilitación (R3).
  - Tests en `apps/dian/tests_evento.py`.
- **R3. WS**: `enviar_evento` (`SendEventUpdateStatus`) y `consultar_eventos`
  (`GetStatusEvent`) en `soap.py`, con tests del sobre. Prueba real en
  habilitación: un 030 sobre una factura validada.
- **R4. Eventos del adquiriente**: modelo `rec_evento` (FK a `rec_documento`)
  con código, número, CUDE, fecha y hora, persona que recibe, concepto del
  reclamo, estado ante la DIAN, XML firmado y respuesta. El servicio valida el
  orden, los plazos y la exclusión entre 031 y 033 antes de ir a la DIAN.
  - Numeración: consecutivo por emisor y tipo de evento, con prefijo por código.
  - El 030 se emite solo al registrar el documento (en `procesar_correo`), salvo
    que el emisor lo tenga apagado.
  - El emisor configura una persona que recibe por defecto (030 y 032). La
    petición puede reemplazarla.
  - API: `POST /api/recepcion/documento/<id>/evento/` con `{codigo, ...}` y
    `GET /api/recepcion/evento/`.
  - `rec_documento` gana un resumen del estado RADIAN.
- **R5. Notificación al proveedor**: el `AttachedDocument` del evento por Zinc,
  con el asunto reglamentario.

## Decisiones

| Tema | Decisión |
|---|---|
| Nombres de tablas | `rec_correo`, `rec_documento`, `rec_adjunto` y `rec_evento` (modelos `Correo`, `Documento`, `Adjunto` y `Evento`) |
| Integración con el ERP | Ninguna: el recibo vive solo en nobelio. Se descartaron el webhook `documento_recibido` (`rec_aviso`) y el `evento_radian` (decidido 2026-10-06) |
| `rec_documento` aparte de `doc_documento` | Los roles están invertidos (allá el emisor factura, aquí recibe) y las acciones de emisión no aplican. Se reusan los catálogos `DocumentoTipo` y `Moneda` |
| Emisor del documento | El del NIT receptor del XML, no el del buzón |
| Archivos | Todos en `rec_adjunto` (también el XML y el PDF del documento), con `correo` siempre y `documento` opcional. Se descartó una tabla de archivos genérica (`arc_archivo`) |
| Adjuntos guardados | Los archivos finales, no los ZIP que los envolvían. Sin deduplicar por hash |
| Tests | `django.test.TestCase` con `manage.py test`, como el resto. Sin pytest |
| Idempotencia del endpoint | SHA-256 del body. `X-Raw-Key` no sirve porque cambia en cada entrega |
| CUFE repetido | Se ignora: CUFE único en `rec_documento` y, si ya existe, no se crea nada ni se marca (decidido 2026-10-03) |
| Carpeta en R2 | Fecha en UTC (`AAAA-MM-DD/<uuid>.eml`): un correo después de las 19:00 de Colombia cae en la del día siguiente. Solo organiza; la fecha que cuenta es `recibido_en` |
| Numeración de eventos RADIAN | Consecutivo en nobelio por emisor y tipo de evento, con prefijo por código. (decidido 2026-10-06) |
| Acuse (030) | Automático al recibir la factura, con opción en el emisor para apagarlo |
| Persona que recibe (030/032) | Un valor por defecto en el emisor que la petición puede reemplazar |
| Alcance RADIAN | Solo como adquiriente (030–033 sobre `rec_documento`), operado desde nobelio, sin ERP ni webhook. Nada sobre las facturas emitidas: ni 034, ni consulta de eventos de los clientes, ni bloqueo de notas (decidido 2026-10-06) |
| Tabla de eventos RADIAN | `rec_evento`, con FK a `rec_documento`. `doc_documento_evento` no tiene nada que ver: es la bitácora de estados de la emisión ante la DIAN |
| Festivos | Catálogo propio (`Festivo`), mantenido a mano cada año. Se descartó la librería `holidays` |
| Facturas con eventos | Cualquier factura 01, sin filtrar por forma de pago. Si la DIAN rechaza, se ajusta en habilitación |
| MIME crudo | En R2 (vinculación nativa del Worker), no en B2. Se evaluó B2 vía API S3 desde el Worker y se descartó (2026-10-03) |
| Token | Obligatorio y falla cerrado. La primera versión fue abierta, a propósito, para probar el flujo |
| Asociación con la empresa | El buzón es el NIT del emisor sin DV (`901192048@recepcion.rededoc.co`). Se asocia en el endpoint; sin campo ni tabla aparte |

## Probar el endpoint

Local (`manage.py runserver`, con `INBOUND_TOKEN` en el `.env`):

```bash
curl -i -X POST http://localhost:8000/recepcion/inbound \
  -H "Authorization: Bearer $INBOUND_TOKEN" \
  -H "Content-Type: message/rfc822" \
  -H "X-Envelope-To: 901192048@recepcion.rededoc.uk" \
  -H "X-Envelope-From: facturas@proveedor.test" \
  -H "X-Raw-Key: 2026-10-02/prueba.eml" \
  --data-binary @correo.eml
```

La primera vez responde `201 {"id": n, "estado": "pendiente"}` y la misma
petición repetida, `200` con el mismo id. En pruebas es la misma petición
contra `https://api.rededoc.uk/recepcion/inbound`.

```bash
python manage.py test apps.recepcion
```
