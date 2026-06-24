# Arquitectura de autenticación

Estado: **implementado** (2026-06-24).

El servicio tiene dos tipos de clientes con necesidades opuestas, así que usa
**dos mecanismos de autenticación que coexisten** en DRF. Cada petición se
autentica con el método que traiga en la cabecera; ninguno depende de sesiones
ni de cookies (todo *stateless*, sin CSRF).

| Cliente | Naturaleza | Mecanismo |
|---|---|---|
| **ERP** | Máquina ↔ máquina, sin humano, larga duración | **API Key** ligada a un emisor |
| **Frontend** | SPA en otro dominio (`app.*` → `api.*`), usuarios humanos | **JWT** (access corto + refresh) |

Regla de oro: **la API Key nunca viaja al frontend; el JWT nunca se usa en el ERP.**

```python
# config/settings/base.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.seguridad.autenticacion.LlaveApiAuthentication",        # ERP
        "rest_framework_simplejwt.authentication.JWTAuthentication",  # frontend
    ],
    ...
}
```

> Al migrar a este esquema se retira `SessionAuthentication` (y, en consecuencia,
> `SessionMiddleware` y `django.contrib.sessions`), ya que el frontend es una SPA
> cross-origin y no hay admin. El `api/auth/` actual (login navegable de DRF)
> también desaparece.

---

## 1. ERP → API Key (ligada a emisor)

Los documentos pertenecen a un emisor, así que la clave **scopea para qué emisor
puede emitir el ERP**. Es revocable de forma independiente.

Modelo nuevo en `apps.seguridad` (nombres en español, según convención):

```
LlaveApi
  emisor       FK -> emisores.Emisor
  nombre       str        # "ERP producción", etc.
  prefijo      str(8)     # identificador público, para buscar la fila
  clave_hash   str        # hash del secreto; el secreto NUNCA se guarda en claro
  activa       bool
  creada       datetime
  ultimo_uso   datetime (null)
  expira       datetime (null, opcional)
```

- Cabecera: `Authorization: Api-Key <prefijo>.<secreto>`
- El secreto se muestra **una sola vez** al crearla; luego solo queda el hash.
- La `Authentication` class busca por `prefijo`, verifica el hash y deja el
  emisor en `request.auth` para que los permisos validen que el ERP solo emite
  para su propio emisor.
- Revocación = `activa = False` (o borrar la fila).

Implementación: modelo + `BaseAuthentication` propios (~80 líneas). Alternativa
de librería: `djangorestframework-api-key` (aporta hashing y prefijo), pero su
modelo no liga al emisor de fábrica y trae acoplamiento al admin, así que para
este proyecto conviene la versión propia.

---

## 2. Frontend SPA → JWT (`djangorestframework-simplejwt`)

- Login con **email + password** (el `USERNAME_FIELD` del `Usuario` ya es email,
  simplejwt lo respeta automáticamente).
- Endpoints (todo el dominio cuelga de `/api/seguridad/`):
  - `POST /api/seguridad/token/`          → `{ access, refresh }`
  - `POST /api/seguridad/token/refresh/`  → nuevo `access`
  - `POST /api/seguridad/token/verify/`   (opcional)
- Vida de tokens: **access 15–30 min**, **refresh varios días**.
- Almacenamiento recomendado en la SPA: `refresh` en cookie `httpOnly` + `access`
  en memoria (mitiga XSS). El `access` se manda como `Authorization: Bearer <access>`.

### CORS (obligatorio por ser cross-origin)

Como la SPA vive en otro dominio, hay que añadir `django-cors-headers`:

```python
CORS_ALLOWED_ORIGINS = ["https://app.midominio.com"]
# Si el refresh va en cookie httpOnly: CORS_ALLOW_CREDENTIALS = True
```

---

## Permisos

- Default `IsAuthenticated` (ya configurado).
- Peticiones del **ERP**: el permiso comprueba que el emisor de la `LlaveApi`
  coincide con el emisor del documento que se intenta emitir.
- Peticiones del **frontend**: permisos por usuario (p. ej. crear usuarios sigue
  siendo solo-staff, ver `apps/seguridad/views/usuario.py`).

## Dependencias

- `djangorestframework-simplejwt==5.5.1`
- `django-cors-headers==4.9.0`

## Mapa de la implementación

| Pieza | Ubicación |
|---|---|
| Modelo `LlaveApi` (+ `generar`, `esta_vigente`, `verificar_secreto`) | `apps/seguridad/models/llave_api.py` |
| Autenticación API Key + `PrincipalLlaveApi` | `apps/seguridad/autenticacion.py` |
| API de gestión de llaves (solo staff) | `apps/seguridad/views/llave_api.py`, ruta `/api/seguridad/llaves-api/` |
| API de usuarios (solo staff) | `apps/seguridad/views/usuario.py`, ruta `/api/seguridad/usuarios/` |
| Alta de llave por CLI | `python manage.py crear_llave_api --emisor <id> --nombre "..."` |
| Rutas de seguridad (router + JWT) | `apps/seguridad/urls.py` (montado en `/api/seguridad/`) → `usuarios`, `llaves-api`, `token/`, `token/refresh/`, `token/verify/` |
| Auth classes, `SIMPLE_JWT`, CORS | `config/settings/base.py` |
| Variables de entorno | `.env.example` (`CORS_ALLOWED_ORIGINS`, `JWT_ACCESS_MINUTOS`, …) |
| Pruebas | `apps/seguridad/tests_autenticacion.py` |

## Notas

- Sin credenciales la API responde **401** (antes daba 403 con `SessionAuthentication`).
- Se retiraron `SessionMiddleware`, `AuthenticationMiddleware`,
  `django.contrib.sessions` y el `api/auth/` navegable: la API es 100% stateless.
- **Producción**: `DJANGO_SECRET_KEY` debe tener ≥32 caracteres, porque también
  firma los JWT (con una clave corta `pyjwt` emite `InsecureKeyLengthWarning`).

## Pendiente (siguiente iteración)

- Scoping por emisor en la creación de documentos: validar que el emisor de la
  `LlaveApi` (`request.user.emisor`) coincide con el del documento que emite el ERP.
