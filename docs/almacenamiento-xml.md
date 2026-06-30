# Plan: mover los XML a object storage (B2) en vez de la BD

## Contexto y problema
A escala (muchos emisores/cuentas, **cientos de millones de documentos**) guardar el
XML firmado y la respuesta DIAN como `TextField` dentro de `doc_documento` no escala:

- Un XML UBL firmado pesa ~15–40 KB. 100M docs × ~30 KB ≈ **~3 TB** dentro de PostgreSQL.
- Aunque PostgreSQL use TOAST (texto >2KB fuera de la fila, comprimido), sigue estando
  en la BD: **backups y restauraciones lentos, WAL/replicación pesados, vacuum costoso**,
  y storage transaccional caro por GB para datos que casi nunca se leen.
- El XML se consulta rara vez (descargar, reenviar, auditar) → no necesita estar en la
  BD transaccional.

## Estado actual
`apps/documentos/models/documento.py`:
- `xml_firmado = TextField` (XML UBL firmado, string).
- `respuesta_dian = TextField` (SOAP crudo de la DIAN).

Ya existe infraestructura B2 reutilizable:
- `apps/utilidades/almacenamiento.py` → `almacenamiento_backblaze()` (S3Storage a B2; **si B2
  no está configurado, cae a `default_storage` local** → dev/tests funcionan sin nube).
- Patrón de referencia: `apps/emisores/models/certificado.py` (`FileField(storage=almacenamiento_backblaze, upload_to=ruta_*)`).

## Diseño propuesto

### Qué queda en cada sitio
| PostgreSQL (`doc_documento`) | Object storage (B2) |
|---|---|
| metadatos de consulta: `cufe_cude`, `estado`, `track_id`, `numero`, totales, FKs, fechas | `xml_archivo` (XML firmado) |
| resultado DIAN parseado: `codigo_estado`, `descripcion`, `errores` (JSON) | `respuesta_archivo` (SOAP crudo, para auditoría) |
| **rutas** a los archivos en B2 (FileField) | `pdf` (opcional, cacheado) |

### Ruta en el bucket (aislada por tenant)
```
<cuenta_id>/<emisor_id>/<aaaa>/<mm>/<numero>-<cufe8>.xml
<cuenta_id>/<emisor_id>/<aaaa>/<mm>/<numero>-<cufe8>.dian.xml
```
(`cufe8` = primeros 8 hex del CUFE, evita colisiones; bucket privado, URLs firmadas).

## Cambios concretos

### 1. Modelo (`documento.py`)
- Reemplazar `xml_firmado: TextField` → `xml_archivo = FileField(storage=almacenamiento_backblaze, upload_to=ruta_xml, blank=True)`.
- Reemplazar `respuesta_dian: TextField` → `respuesta_archivo = FileField(...)` **y** añadir columnas parseadas para poder consultar/filtrar sin abrir el archivo:
  - `codigo_estado = CharField(blank=True)`
  - `descripcion_estado = CharField(blank=True)`
  - `errores = JSONField(default=list, blank=True)`
- Funciones `ruta_xml(instance, filename)` / `ruta_respuesta(...)` con el esquema de arriba.
- Helper para leer bytes: `documento.leer_xml()` → `self.xml_archivo.open("rb").read()`.

> Nota: `xml_firmado` deja de ser un string. Hoy se accede como string en varios sitios
> (ver §3); por eso se renombra a `xml_archivo` (FileField) para que el cambio sea explícito.

### 2. Servicios (`apps/dian/servicios.py`)
- `generar_y_firmar`: en vez de `documento.xml_firmado = xml.decode()`, guardar el archivo:
  `documento.xml_archivo.save(nombre, ContentFile(xml_firmado), save=False)` y luego `save(update_fields=[...])`.
- `enviar_a_dian` / `consultar_estado`: leer el XML con `documento.leer_xml()`; guardar
  `respuesta_archivo` (crudo) **+** persistir `codigo_estado`, `descripcion_estado`,
  `errores` parseados (hoy solo se devuelven en la respuesta de la acción, no se guardan).
- La guarda `if not documento.xml_firmado` → `if not documento.xml_archivo`.

### 3. Vistas/serializers (`apps/documentos/`)
- Acción `/xml/`: `StreamingHttpResponse` desde `documento.xml_archivo.open()` (o redirect a
  URL firmada de B2). API externa **sin cambios** para el cliente.
- Acción `/pdf/`: igual (se puede cachear en B2 tras generarlo la 1ª vez).
- Serializer de lectura: exponer `codigo_estado`, `descripcion_estado`, `errores` (ya
  persistidos) y, si se quiere, una URL de descarga del XML.

### 4. Migración (datos preservados)
Esquema en pasos (un solo release, varias operaciones):
1. `AddField` `xml_archivo`, `respuesta_archivo`, `codigo_estado`, `descripcion_estado`, `errores` (todos opcionales).
2. `RunPython`: por cada documento con `xml_firmado`/`respuesta_dian`, escribir el contenido
   a B2 (vía los nuevos FileField) y parsear la respuesta a las columnas nuevas.
3. `RemoveField` `xml_firmado`, `respuesta_dian`.

(Hoy son 3 documentos → trivial; el `RunPython` queda escrito para cualquier volumen.)

### 5. Consistencia BD ↔ B2
- Igual que con el `.p12`: si falla la escritura en B2, no se persiste el estado.
- Borrado de documento → borrar el objeto en B2 (señal `post_delete` o en el flujo de borrado).
- En dev/tests sin B2: cae a almacenamiento local automáticamente (sin configurar nada).

## Fuera de alcance (relacionado, futuro)
- **Particionar** `doc_documento` (por fecha o por cuenta) para cientos de millones de filas.
- Política de **retención/lifecycle** en B2 (la DIAN exige conservar años).
- Firmar URLs de descarga con expiración para el frontend.

## Decisiones abiertas
1. ¿`/xml/` y `/pdf/` hacen *stream* desde el backend o **redirect a URL firmada** de B2
   (descarga directa del cliente, menos carga en el backend)?
2. ¿Guardar también la respuesta DIAN cruda en B2, o basta con las columnas parseadas
   (`codigo_estado`, `descripcion`, `errores`) y descartar el SOAP crudo?
3. ¿Cachear el PDF en B2 o seguir generándolo al vuelo siempre?

## Impacto / riesgo
- Cambio de contrato **interno** (campo `xml_firmado` → `xml_archivo` + helper); la **API
  pública no cambia** (`/xml/`, `/pdf/` siguen igual).
- Migración con `RunPython` de datos (reversible escribiendo el reverso).
- Pruebas: ya funcionan con storage local, no requieren B2.
