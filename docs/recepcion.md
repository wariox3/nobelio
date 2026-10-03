# Recepción de facturas de proveedores

Estado: **en construcción, por pasos**. Iniciado el 2026-10-02. Hoy los correos
llegan y se registran; todavía no se abren ni se procesan.

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
                                    (pendiente) Celery: procesa el correo
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
  - No expone `sha256` ni `raw_key`. Tests en `apps/recepcion/tests_api.py`.
  - `DELETE /api/recepcion/correo/<id>/` elimina un correo **sin emisor** (el de
    una empresa que no está ni va a estar en la plataforma): la fila y su MIME
    en R2 (`apps/recepcion/r2.py`), o ninguno de los dos. Un correo con emisor
    responde 400; sin las variables `R2_*`, 503, y si R2 falla, 502 y la fila
    se queda. Como los correos sin emisor solo los ve el staff o una llave de
    alcance global, en la práctica solo ellos pueden eliminarlos.
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

1. **Procesar el correo en Celery, sin abrir adjuntos.**
   - El endpoint encola `procesar_correo` en una cola nueva, `recepcion`
     (agregarla a `CELERY_TASK_ROUTES` y al `-Q` del worker en
     `docs/despliegue.md`).
   - El worker descarga el MIME de R2 por `raw_key` (boto3 contra el endpoint
     S3 de R2) y lee `Message-ID` y asunto.
   - Detecta la confirmación de reenvío de Gmail (`forwarding-noreply@google.com`)
     y guarda el código o enlace en `confirmacion_reenvio`.
   - Reintenta los errores transitorios con backoff, máximo 5 (`intentos`).
   - Usa las variables `R2_*` y el cliente de `apps/recepcion/r2.py`, que ya
     existen para el borrado.
2. **Adjuntos y documentos.**
   - Extraer XML sueltos, ZIP (también anidados) y `.eml` o `message/rfc822`
     adjuntos.
   - Parsear el AttachedDocument (factura embebida en el CDATA de
     `cac:Attachment/cac:ExternalReference/cbc:Description`, y la
     ApplicationResponse de la DIAN), Invoice, CreditNote y DebitNote.
   - Modelo `Documento` (`rec_documento`) con los datos extraídos, y XML, XML de
     la factura y PDF guardados en B2 (`almacenamiento_backblaze`).
   - Validar el NIT receptor contra el del emisor. Si el correo llegó sin
     emisor (el alias no es un NIT registrado) pero el NIT receptor sí lo es,
     asociarlo y anotar `alias_no_registrado`.
   - Sin coincidencia por ninguna de las dos vías: `empresa_desconocida`.
3. **Comando `reprocesar_correos`**: `--id <n>` o `--todos` (los correos en
   error, pendiente o empresa_desconocida), descargando de R2.
4. **API de documentos.** `GET /api/recepcion/documento/`, con los mismos
   filtros que la de correos y las descargas `xml/`, `xml-factura/` y `pdf/`.
   La de correos ya existe; ahí se le suman sus documentos.
5. **Webhook al ERP** (`rec_aviso`). Una bandera nueva `documento_recibido` en
   `emi_webhook`, firmada con el `firmar` de `apps/emisores/servicios/webhooks.py`.
   Con reintentos y backoff, a diferencia de los avisos de emisión. El contrato
   (`tipo: "documento_recibido"`) hay que agregarlo en
   `torio/docs/webhook_rededoc.md`.
6. **Producción**: repetir lo de Cloudflare en `rededoc.co` y apuntar el Worker
   a `api.rededoc.co`.

Fuera de alcance: eventos RADIAN (030, 031, 032 y 033). El documento tiene pk
UUID y CUFE; los eventos irán en una tabla aparte con FK al documento.

## Decisiones

| Tema | Decisión |
|---|---|
| Nombres de tablas | `rec_correo`, `rec_documento` y `rec_aviso` (modelos `Correo`, `Documento` y `Aviso`) |
| Tests | `django.test.TestCase` con `manage.py test`, como el resto. Sin pytest |
| Idempotencia del endpoint | SHA-256 del body. `X-Raw-Key` no sirve porque cambia en cada entrega |
| Carpeta en R2 | Fecha en UTC (`AAAA-MM-DD/<uuid>.eml`): un correo después de las 19:00 de Colombia cae en la del día siguiente. Solo organiza; la fecha que cuenta es `recibido_en` |
| MIME crudo | En R2 (vinculación nativa del Worker), no en B2. Se evaluó B2 vía API S3 desde el Worker y se descartó (2026-10-03) |
| Token | Obligatorio y falla cerrado. La primera versión fue abierta, a propósito, para probar el flujo |
| Asociación con la empresa | El buzón es el NIT del emisor sin DV (`901192048@recepcion.rededoc.co`). Se asocia en el endpoint; sin campo ni tabla aparte |

### Por decidir (antes del paso indicado)

- **Origen del MIME en dev** (paso 1). La propuesta es que, sin las variables
  `R2_*`, el endpoint guarde el body en un `mime_archivo` local, para poder
  probar sin R2. En pruebas y producción siempre se descarga de R2.
- **CUFE duplicado** (paso 2). El pedido original quiere un CUFE único y a la vez
  un estado `duplicado`, y las dos cosas chocan. La propuesta es una unicidad
  condicional: CUFE único entre `recibido` y `receptor_no_coincide`, con filas
  `duplicado` que apuntan al original y el par (correo, cufe) único para que
  reprocesar no repita filas.

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
