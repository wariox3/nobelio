# Nobelio — Servicio de Facturación Electrónica DIAN (Colombia)

Servicio en **Django + DRF** para emitir documentos electrónicos ante la DIAN:
**factura de venta, notas crédito/débito y documento soporte**. Implementa el
pipeline completo conforme al Anexo Técnico v1.9 (Resolución DIAN 000165/2023):

```
Documento → XML UBL 2.1 → CUFE/CUDE → Firma XAdES-EPES → Envío WS DIAN → PDF+QR
```

Todo el pipeline está cubierto por pruebas y validado contra los **XSD oficiales**
de la DIAN; el CUFE/CUDE se verifica contra los ejemplos oficiales del Anexo.

---

## Tabla de contenido

- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración (.env)](#configuración-env)
- [Puesta en marcha](#puesta-en-marcha)
- [Flujo de uso completo (API)](#flujo-de-uso-completo-api)
- [Comandos de gestión](#comandos-de-gestión)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Pruebas](#pruebas)
- [Pendientes para producción](#pendientes-para-producción)

---

## Características

- **Catálogos DIAN** cargados desde las listas oficiales Genericode (`.gc`).
- **Generación XML UBL 2.1** para los 4 tipos de documento, validada contra XSD.
- **CUFE/CUDE** (SHA-384) según el Anexo Técnico (verificado con los ejemplos oficiales).
- **Firma XAdES-EPES** con certificado `.p12` (verificada criptográficamente).
- **Cliente SOAP** de los Web Services DIAN con WS-Security (Set de Pruebas y producción).
- **Representación gráfica PDF** con código QR.
- **API REST** (DRF) que orquesta todo el ciclo de vida.

---

## Requisitos

- Python 3.12+
- Dependencias en `requirements.txt` (Django 5.1, DRF, lxml, cryptography,
  reportlab, qrcode, requests, …).
- Un certificado digital `.p12` emitido por una entidad autorizada (para firmar).

---

## Instalación

> ¿Primera vez montando el proyecto? Ver
> [docs/entorno-desarrollo.md](docs/entorno-desarrollo.md) para los pasos
> completos desde cero (PostgreSQL, venv, `.env`).

```bash
# 1. Clonar y entrar al proyecto
cd nobelio

# 2. Crear y activar un entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración (.env)

Copia la plantilla y ajusta los valores:

```bash
cp .env.example .env
```

Variables principales (`config/settings/base.py` las lee con `django-environ`):

| Variable | Descripción | Por defecto |
|----------|-------------|-------------|
| `DJANGO_SECRET_KEY` | Clave secreta de Django | inseguro (cámbiala) |
| `DEBUG` | Modo depuración | `True` (dev) |
| `ALLOWED_HOSTS` | Hosts permitidos (CSV) | `localhost,127.0.0.1` |
| `DATABASE_URL` | Conexión PostgreSQL (obligatorio) | — |
| `DIAN_ENVIRONMENT` | `2` habilitación / `1` producción | `2` |
| `DIAN_POLICY_ID` | URL de la política de firma | política v2 DIAN |
| `DIAN_POLICY_HASH` | SHA-256 (base64) del PDF de la política | *(vacío — configúralo)* |

> Los settings se dividen en `config/settings/{base,dev,prod}.py`.
> Por defecto se usa `config.settings.dev`.

---

## Puesta en marcha

```bash
# Migraciones
python manage.py migrate

# Cargar los catálogos DIAN (tipos, tributos, municipios, monedas, …)
python manage.py cargar_catalogos

# Crear el usuario staff inicial (da de alta cuentas, usuarios y llaves)
python manage.py createsuperuser

# Levantar el servidor de desarrollo
python manage.py runserver
```

- API: `http://localhost:8000/api/`
- Estado: `http://localhost:8000/estado/`

> No hay sitio de administración: `django.contrib.admin` no está instalado y la
> API es *stateless* (sin sesiones ni cookies). Todo se hace por la API o por
> comandos de gestión.

Inspeccionar catálogos sin BD:

```bash
python manage.py listas                  # resumen de las listas .gc
python manage.py listas TipoResponsabilidad
```

---

## Flujo de uso completo (API)

> **Paso a paso (alta → emisión):** ver [docs/checklist-emision.md](docs/checklist-emision.md).

> Autenticación: el frontend usa **JWT** (`POST /api/seguridad/token/` →
> `Authorization: Bearer <access>`) y el ERP usa **API Key**
> (`Authorization: Api-Key <prefijo>.<secreto>`). Ver [docs/autenticacion.md](docs/autenticacion.md).
> Cada credencial solo alcanza los emisores de su cuenta: lo ajeno no aparece en
> los listados y responde 404. Los catálogos (`/api/catalogos/...`) son de solo
> lectura.

### 0. Obtener una credencial

El staff crea la **cuenta** (el tenant) y su **llave de API**; el emisor cuelga
siempre de una cuenta. Desde la línea de comandos:

```bash
python manage.py crear_llave_api --cuenta 1 --nombre "ERP producción"
# → Authorization: Api-Key <prefijo>.<secreto>   (se muestra una sola vez)

export API_KEY='<prefijo>.<secreto>'
```

### 1. Consultar catálogos (para obtener los IDs)

```bash
curl -H "Authorization: Api-Key $API_KEY" \
  "http://localhost:8000/api/catalogos/tipo-identificacion/"
curl -H "Authorization: Api-Key $API_KEY" \
  "http://localhost:8000/api/catalogos/tributo/?search=IVA"
curl -H "Authorization: Api-Key $API_KEY" \
  "http://localhost:8000/api/catalogos/municipio/?search=Medell"
```

### 2. Crear el emisor (OFE)

```bash
curl -X POST http://localhost:8000/api/emisores/emisor/ \
  -H "Content-Type: application/json" -H "Authorization: Api-Key $API_KEY" \
  -d '{
    "razon_social": "Empresa Demo SAS",
    "tipo_identificacion": 1,
    "numero_identificacion": "700085371",
    "digito_verificacion": "1",
    "tipo_organizacion": 1,
    "responsabilidades": [],
    "pais": "CO", "departamento": "05", "municipio": "05001",
    "direccion": "Calle 1 # 2-3",
    "correo": "facturacion@empresa.co"
  }'
```

`pais`, `departamento` y `municipio` van por **código** (ISO 3166 y DANE), no
por id: el id es un serial de cada base y cambia entre ambientes. El servidor
resuelve el código contra el catálogo y guarda la fila que corresponde; si el
código no existe, responde 400 en ese campo.

La `cuenta` no se envía: sale de la credencial. El alta no consulta el RUES; lo
que se rechaza es repetir un emisor ya dado de alta en la misma cuenta. Para
comprobar un NIT (y autocompletar el formulario) está
`GET /api/emisores/emisor/validar-nit/?nit=<NIT>`.

Luego registra para ese emisor (ver
[docs/checklist-emision.md](docs/checklist-emision.md) para el detalle):

- **Certificado digital** — `POST /api/emisores/certificado/cargar/` (multipart
  con el `.p12` y su `clave`; se valida y se guarda en Backblaze B2). Va primero:
  es lo que firma todo lo que sigue, y sin él no se puede registrar el software.
- **Software DIAN** — `POST /api/emisores/emisor/crear-habilitacion/`:
  `identificador`, `pin` y `test_set_id` (los que entrega la DIAN). Comprueba que
  el emisor tenga certificado activo y vigente, registra el software y jubila el
  anterior; un solo software activo por emisor. El Set de Pruebas no se corre
  desde aquí: los documentos de habilitación se emiten y envían con los endpoints
  de documentos.
  El `ProviderID` del XML no se guarda: en software propio es el NIT del emisor.
- **Resolución de facturación** — `POST /api/emisores/resolucion/importar-dian/`
  la trae de la DIAN con su `clave_tecnica` (o `POST /api/emisores/resolucion/`
  para cargarla a mano).

### 3. Crear el documento (con receptor, líneas e impuestos)

Los totales se calculan automáticamente a partir de los detalles. La resolución
se indica con `numero_resolucion` —el número que la DIAN le dio al emisor, que
es el que este conoce—; se busca entre las resoluciones **activas** de ese
emisor y se guarda su id. El `prefijo` y el `consecutivo` tienen que caber en lo
que esa resolución autorizó, o la petición responde 400.

Los datos del `adquiriente` van **dentro de cada documento**: no hay cartera de
clientes ni endpoint propio. Cada documento guarda su copia del receptor, que es
la que quedó firmada en el XML y entró en el CUFE.

```bash
curl -X POST http://localhost:8000/api/documentos/documento/ \
  -H "Content-Type: application/json" -H "Authorization: Api-Key $API_KEY" \
  -d '{
    "documento_tipo": "<id-tipo>",
    "emisor": "<id-emisor>",
    "numero_resolucion": "18760000001",
    "adquiriente": {
      "razon_social": "Cliente Demo",
      "tipo_identificacion": 1,
      "numero_identificacion": "800199436",
      "digito_verificacion": "6",
      "tipo_organizacion": 1, "pais": 1
    },
    "prefijo": "SETP", "consecutivo": 990000001, "numero": "SETP990000001",
    "fecha_emision": "2026-06-21", "hora_emision": "10:00:00",
    "moneda": 1,
    "detalles": [
      {
        "numero_linea": 1, "descripcion": "Producto demo",
        "cantidad": "1", "unidad_medida": 1,
        "valor_unitario": "1000000", "valor_total": "1000000.00",
        "impuestos": [
          {"tributo": 1, "base_gravable": "1000000.00", "tarifa": "19.00", "valor": "190000.00"}
        ]
      }
    ]
  }'
```

`documento_tipo` es el **id** de la fila de `DocumentoTipo` cuyo `codigo` es
`factura_venta`, `nota_credito`, `nota_debito`, `documento_soporte` o `nomina`.

### 4. Emitir (genera XML UBL + CUFE + firma)

```bash
curl -X POST http://localhost:8000/api/documentos/documento/<id>/emitir/ \
  -H "Authorization: Api-Key $API_KEY"
# → { "estado": "firmado", "cufe_cude": "8bb918b1...f5bd9b4" }
```

### 5. Enviar a la DIAN

Se usa `SendTestSetAsync` (con el `test_set_id` del software) solo mientras se
está en habilitación (`DIAN_ENVIRONMENT=2`) **y** el Set de Pruebas todavía no
ha sido aceptado. En cuanto la DIAN lo acepta hay que marcar
`SoftwareDian.set_pruebas_aceptado` a mano, y a partir de ahí —o en producción—
se usa `SendBillSync` (síncrono).

```bash
curl -X POST http://localhost:8000/api/documentos/documento/<id>/enviar/ \
  -H "Authorization: Api-Key $API_KEY"
# → { "estado": "...", "track_id": "...", "es_valido": true/false, "errores": [...] }
```

El documento queda en `aceptado`, `rechazado` o —si la DIAN aún no ha
resuelto— `enviado`. En ese último caso:

```bash
# Consulta sin efectos: devuelve lo que dice la DIAN, no toca el documento.
curl -H "Authorization: Api-Key $API_KEY" \
  http://localhost:8000/api/documentos/documento/<id>/consultar/

# Aplica el resultado al documento (solo si está enviado/rechazado).
curl -X POST -H "Authorization: Api-Key $API_KEY" \
  http://localhost:8000/api/documentos/documento/<id>/actualizar-estado/
```

### 6. Descargar artefactos

```bash
curl -H "Authorization: Api-Key $API_KEY" \
  http://localhost:8000/api/documentos/documento/<id>/xml/ -o factura.xml
curl -H "Authorization: Api-Key $API_KEY" \
  http://localhost:8000/api/documentos/documento/<id>/pdf/ -o factura.pdf
```

### Notas crédito/débito y documento soporte

Mismo flujo cambiando `documento_tipo`:

- `nota_credito` / `nota_debito`: requieren `documento_referencia` (el id de la
  factura que corrigen). Usan CUDE.
- `documento_soporte`: para adquisiciones a no obligados a facturar. Usa CUDE.

---

## Comandos de gestión

| Comando | Descripción |
|---------|-------------|
| `python manage.py cargar_catalogos` | Carga las listas DIAN `.gc` en la BD (idempotente). |
| `python manage.py listas [Nombre]` | Inspecciona las listas de valores `.gc`. |
| `python manage.py crear_llave_api --cuenta <id> --nombre "..."` | Crea la API Key de una integración (muestra el secreto una sola vez). |
| `python manage.py emitir_documento <uuid> [--enviar]` | Firma y (opcional) envía un documento a la DIAN. |

---

## Estructura del proyecto

```
nobelio/
├── config/                  Proyecto Django (settings, urls)
│   ├── settings/            base.py · dev.py · prod.py
│   └── urls.py              Monta cada app bajo /api/<dominio>/
├── apps/
│   ├── nucleo/              Modelos base abstractos + errores de la API
│   ├── cuentas/             Cuenta (tenant): agrupa emisores y llaves
│   ├── seguridad/           Usuario (JWT), LlaveApi (ERP) y alcance multi-inquilino
│   ├── utilidades/          Almacenamiento en B2 y cliente RUES
│   ├── catalogos/           Catálogos DIAN + parser Genericode (.gc)
│   │   ├── genericode.py    Parser de listas .gc
│   │   └── datos/listas/    Listas oficiales DIAN (.gc)
│   ├── emisores/            Emisor (OFE), software, certificado, resolución
│   ├── documentos/          Documento electrónico, detalles, impuestos, receptor, API
│   └── dian/                Núcleo DIAN:
│       ├── identificadores.py   CUFE / CUDE / código de seguridad
│       ├── ubl.py              Generación XML UBL 2.1 (factura, notas, soporte)
│       ├── firma.py            Firma XAdES-EPES
│       ├── soap.py             Cliente SOAP + WS-Security
│       ├── representacion.py   PDF + QR
│       ├── servicios.py        Orquestación del pipeline
│       └── datos/xsd/          Esquemas XSD oficiales DIAN
├── docs/
│   ├── checklist-emision.md    Del alta del tenant a la emisión, paso a paso
│   ├── autenticacion.md        JWT, API Key y alcance multi-inquilino
│   ├── anexo-tecnico.md        Resumen del Anexo Técnico v1.9
│   ├── almacenamiento-xml.md   XML firmado y respuestas DIAN en B2
│   ├── entorno-desarrollo.md   Montar el proyecto desde cero
│   └── despliegue.md           Puesta en producción en un VPS
├── requirements.txt
└── manage.py
```

---

## Pruebas

```bash
python manage.py test          # toda la suite
python manage.py test apps.dian  # solo el núcleo DIAN
```

La suite cubre: parser de catálogos, CUFE/CUDE (contra ejemplos oficiales),
generación UBL (validada contra XSD), firma XAdES (verificada criptográficamente),
cliente SOAP (WS-Security), PDF, servicios y la API end-to-end.

---

## Pendientes para producción

Estos puntos solo se confirman al integrar contra el ambiente real de la DIAN:

1. Configurar `DIAN_POLICY_HASH` con el SHA-256 (base64) del PDF de la política
   de firma (`apps/dian/firma.calcular_hash_politica()` lo calcula).
2. Cargar el `.p12`, el `test_set_id` y las claves técnicas reales del emisor.
3. Validar contra el **Set de Pruebas** (posibles ajustes de canonicalización
   exclusiva / `X509IssuerName` en la firma del sobre SOAP).
4. Afinar los roles del **documento soporte** y el QR en **todas las páginas** del PDF.

---

> Documentación oficial de referencia: *Caja de Herramientas FE V19 (v2026)* de la DIAN
> (Anexo Técnico, XSD, listas de valores y guía de Web Services).
