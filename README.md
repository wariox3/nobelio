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

# Crear un usuario administrador (la API requiere autenticación)
python manage.py createsuperuser

# Levantar el servidor de desarrollo
python manage.py runserver
```

- API: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`
- Estado: `http://localhost:8000/estado/`

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
> Los catálogos (`/api/catalogos/...`) son de solo lectura.

### 1. Consultar catálogos (para obtener los IDs)

```bash
curl "http://localhost:8000/api/catalogos/tipos-identificacion/"
curl "http://localhost:8000/api/catalogos/tributos/?search=IVA"
curl "http://localhost:8000/api/catalogos/municipios/?search=Medell"
```

### 2. Crear el emisor (OFE)

```bash
curl -X POST http://localhost:8000/api/emisores/ \
  -H "Content-Type: application/json" -b cookies.txt \
  -d '{
    "razon_social": "Empresa Demo SAS",
    "tipo_identificacion": 1,
    "numero_identificacion": "700085371",
    "digito_verificacion": "1",
    "tipo_organizacion": 1,
    "responsabilidades": [],
    "pais": 1, "departamento": 1, "municipio": 1,
    "direccion": "Calle 1 # 2-3",
    "correo": "facturacion@empresa.co"
  }'
```

Luego, en el **admin** (o por shell), registra para ese emisor:

- **Software DIAN** (`SoftwareDian`): `identificador`, `pin`, `id_proveedor`,
  y `test_set_id` (entregado por la DIAN para habilitación).
- **Certificado digital** (`CertificadoDigital`): sube el `.p12` y su `clave`.
- **Resolución de facturación** (`ResolucionFacturacion`): prefijo, rango,
  `clave_tecnica` y vigencia.

### 3. Crear el adquirente (cliente)

```bash
curl -X POST http://localhost:8000/api/adquirentes/ \
  -H "Content-Type: application/json" -b cookies.txt \
  -d '{
    "razon_social": "Cliente Demo",
    "tipo_identificacion": 1,
    "numero_identificacion": "800199436",
    "tipo_organizacion": 1, "pais": 1
  }'
```

### 4. Crear el documento (con líneas e impuestos)

Los totales se calculan automáticamente a partir de las líneas.

```bash
curl -X POST http://localhost:8000/api/documentos/ \
  -H "Content-Type: application/json" -b cookies.txt \
  -d '{
    "tipo": "factura_venta",
    "emisor": "<id-emisor>",
    "resolucion": "<id-resolucion>",
    "adquirente": "<id-adquirente>",
    "prefijo": "SETP", "consecutivo": 990000001, "numero": "SETP990000001",
    "fecha_emision": "2026-06-21", "hora_emision": "10:00:00",
    "moneda": 1,
    "lineas": [
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

### 5. Emitir (genera XML UBL + CUFE + firma)

```bash
curl -X POST http://localhost:8000/api/documentos/<id>/emitir/ -b cookies.txt
# → { "estado": "firmado", "cufe_cude": "8bb918b1...f5bd9b4" }
```

### 6. Enviar a la DIAN

En habilitación (`DIAN_ENVIRONMENT=2`) usa `SendTestSetAsync` con el `test_set_id`;
en producción usa `SendBillSync`.

```bash
curl -X POST http://localhost:8000/api/documentos/<id>/enviar/ -b cookies.txt
# → { "estado": "...", "track_id": "...", "es_valido": true/false, "errores": [...] }
```

### 7. Descargar artefactos

```bash
curl -b cookies.txt http://localhost:8000/api/documentos/<id>/xml/ -o factura.xml
curl -b cookies.txt http://localhost:8000/api/documentos/<id>/pdf/ -o factura.pdf
```

### Notas crédito/débito y documento soporte

Mismo flujo cambiando `"tipo"`:

- `nota_credito` / `nota_debito`: requieren `documento_referencia` (el id de la
  factura que corrigen). Usan CUDE.
- `documento_soporte`: para adquisiciones a no obligados a facturar. Usa CUDE.

---

## Comandos de gestión

| Comando | Descripción |
|---------|-------------|
| `python manage.py cargar_catalogos` | Carga las listas DIAN `.gc` en la BD (idempotente). |
| `python manage.py listas [Nombre]` | Inspecciona las listas de valores `.gc`. |
| `python manage.py emitir_documento <uuid> [--enviar]` | Firma y (opcional) envía un documento a la DIAN. |

---

## Estructura del proyecto

```
nobelio/
├── config/                  Proyecto Django (settings, urls, api router)
│   ├── settings/            base.py · dev.py · prod.py
│   └── api.py               Router DRF (/api/)
├── apps/
│   ├── nucleo/              Modelos base abstractos
│   ├── catalogos/           Catálogos DIAN + parser Genericode (.gc)
│   │   ├── genericode.py    Parser de listas .gc
│   │   └── datos/listas/    Listas oficiales DIAN (.gc)
│   ├── emisores/            Emisor (OFE), software, certificado, resolución
│   ├── documentos/          Documento electrónico, líneas, impuestos, adquirente, API
│   └── dian/                Núcleo DIAN:
│       ├── identificadores.py   CUFE / CUDE / código de seguridad
│       ├── ubl.py              Generación XML UBL 2.1 (factura, notas, soporte)
│       ├── firma.py            Firma XAdES-EPES
│       ├── soap.py             Cliente SOAP + WS-Security
│       ├── representacion.py   PDF + QR
│       ├── servicios.py        Orquestación del pipeline
│       └── datos/xsd/          Esquemas XSD oficiales DIAN
├── docs/
│   └── anexo-tecnico.md     Resumen del Anexo Técnico v1.9
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
