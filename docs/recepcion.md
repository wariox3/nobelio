# Recepción de facturas de proveedores

Estado: **en construcción, por pasos**. Iniciado el 2026-10-02. Hoy los correos
llegan, se registran y se procesan: cabeceras, adjuntos y documentos.

Nobelio recibe por correo las facturas que los proveedores les mandan a los
emisores registrados, extrae los documentos electrónicos (factura, nota crédito,
nota débito) y se los entrega al ERP del emisor. Nobelio no es multitenant: la
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
    una empresa que no está ni va a estar en la plataforma): la fila y su MIME
    en R2 (`apps/recepcion/r2.py`), o ninguno de los dos. Un correo con emisor
    responde 400; sin las variables `R2_*`, 503, y si R2 falla, 502 y la fila
    se queda. Como los correos sin emisor solo los ve el staff o una llave de
    alcance global, en la práctica solo ellos pueden eliminarlos.
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
    emisor factura; aquí recibe). Reusa `DocumentoTipo` y `Moneda`. XML
    recibido, XML del documento y PDF en B2, en
    `<emisor>/recepcion/<aaaa>/<mm>/`.
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
    y `?desde`/`?hasta` sobre `fecha_emision`. `?search=` en número, CUFE, NIT
    y razón social del proveedor; `?ordering=fecha_emision|numero|total_a_pagar|creado_en`.
  - Descargas: `xml/` (como llegó), `xml-factura/` (el documento sin el
    AttachedDocument; si llegó suelto, el mismo de `xml/`) y `pdf/` (400 si el
    proveedor no lo mandó). `tiene_pdf` y `tiene_xml_factura` lo anticipan.
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

1. **Comando `reprocesar_correos`**: `--id <n>` o `--todos` (los correos en
   error, pendiente o empresa_desconocida), descargando de R2.
2. **Webhook al ERP** (`rec_aviso`). Una bandera nueva `documento_recibido` en
   `emi_webhook`, firmada con el `firmar` de `apps/emisores/servicios/webhooks.py`.
   Con reintentos y backoff, a diferencia de los avisos de emisión. El contrato
   (`tipo: "documento_recibido"`) hay que agregarlo en
   `torio/docs/webhook_rededoc.md`.
3. **Producción**: repetir lo de Cloudflare en `rededoc.co` y apuntar el Worker
   a `api.rededoc.co`.

Fuera de alcance: eventos RADIAN (030, 031, 032 y 033). El documento tiene pk
UUID y CUFE; los eventos irán en una tabla aparte con FK al documento.

## Decisiones

| Tema | Decisión |
|---|---|
| Nombres de tablas | `rec_correo`, `rec_documento` y `rec_aviso` (modelos `Correo`, `Documento` y `Aviso`) |
| `rec_documento` aparte de `doc_documento` | Los roles están invertidos (allá el emisor factura, aquí recibe) y las acciones de emisión no aplican. Se reusan los catálogos `DocumentoTipo` y `Moneda` |
| Emisor del documento | El del NIT receptor del XML, no el del buzón |
| Tests | `django.test.TestCase` con `manage.py test`, como el resto. Sin pytest |
| Idempotencia del endpoint | SHA-256 del body. `X-Raw-Key` no sirve porque cambia en cada entrega |
| CUFE repetido | Se ignora: CUFE único en `rec_documento` y, si ya existe, no se crea nada ni se marca (decidido 2026-10-03) |
| Carpeta en R2 | Fecha en UTC (`AAAA-MM-DD/<uuid>.eml`): un correo después de las 19:00 de Colombia cae en la del día siguiente. Solo organiza; la fecha que cuenta es `recibido_en` |
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
