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

- [ ] `GET /api/emisores/emisor/validar-nit/?nit=<NIT>` → valida contra RUES y autocompleta datos.
- [ ] `POST /api/emisores/emisor/` → crea el emisor ligado a la **cuenta**
      (razón social, NIT + DV, tipo de organización, ubicación, responsabilidades).

## 4. Software DIAN (modalidad software propio)

- [ ] `POST /api/emisores/software/` →
  - `id_proveedor` = **NIT del propio emisor** (el proveedor tecnológico es el emisor).
  - `identificador` (SoftwareID) y `pin` reales registrados ante la DIAN.
  - `test_set_id` solo aplica en **habilitación** (Set de Pruebas).

## 5. Certificado digital

- [ ] `POST /api/emisores/certificado/cargar/` (multipart: `emisor`, `archivo` .p12/.pfx, `clave`).
  - Se **valida** antes de guardar: integridad + clave, llave RSA ≥ 2048, vigencia,
    y que el **NIT del certificado coincida** con el del emisor.
  - `vigente_desde`/`vigente_hasta` se autocompletan del propio certificado.
  - Se guarda en **B2** (`<id_emisor>/certificados/`); un único certificado **activo** por emisor
    (cargar uno nuevo jubila el anterior).

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

## 7. Adquirente (cliente receptor)

- [ ] `POST /api/documentos/adquirente/` → datos del receptor del documento.

## 8. Documento electrónico

- [ ] `POST /api/documentos/documento/` (`DocumentoCrearSerializer`):
      `tipo`, `emisor`, `resolucion`, `adquirente`, `moneda`, formas/medios de pago,
      y `lineas` (cada una con sus `impuestos`/tributos). Los totales se calculan al crear.

## 9. Ciclo de vida DIAN

- [ ] `POST /api/documentos/documento/{id}/emitir/` → genera XML UBL 2.1, calcula
      **CUFE/CUDE** y **firma XAdES-EPES** (requiere certificado activo del emisor).
- [ ] `POST /api/documentos/documento/{id}/enviar/` → envía a la DIAN por WS;
      devuelve `track_id`, `es_valido`, `codigo_estado` y errores.
- [ ] `GET /api/documentos/documento/{id}/xml/` → descarga el XML firmado.
- [ ] `GET /api/documentos/documento/{id}/pdf/` → descarga la representación gráfica (PDF con QR).

---

### Resumen de dependencias

```
Cuenta → Usuario
      └→ Emisor → Software DIAN
                → Certificado (B2)
                → Resolución
                                 ┐
Adquirente ──────────────────────┼→ Documento → emitir → enviar → xml/pdf
                                 ┘
```
