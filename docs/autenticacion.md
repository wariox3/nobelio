# Arquitectura de autenticación

Estado: **implementado** (2026-06-24). Alcance por cuenta: **implementado**
(2026-08-05).

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

## 1. ERP → API Key (ligada a una cuenta)

Una integración como RedDoc ERP factura para **muchos emisores** (sus propios
clientes), así que la credencial no cuelga de un emisor sino de la **cuenta**,
que es el propietario de los datos. Una llave por emisor no aportaría nada: las
N llaves vivirían igualmente en el mismo servidor del ERP, así que el radio de
daño ante un compromiso sería el mismo y el coste operativo (provisionar, rotar
y revocar N secretos) se multiplicaría.

```
LlaveApi
  cuenta       FK -> cuentas.Cuenta      # alcance: sus emisores
  nombre       str        # "RedDoc producción", etc.
  prefijo      str(8)     # identificador público, para buscar la fila
  clave_hash   str        # hash del secreto; el secreto NUNCA se guarda en claro
  activa       bool
  expira_en    datetime (null, opcional)
  ultimo_uso_en datetime (null)
```

- Cabecera: `Authorization: Api-Key <prefijo>.<secreto>`
- El secreto se muestra **una sola vez** al crearla; luego solo queda el hash.
- La `Authentication` class busca por `prefijo`, verifica el hash y deja un
  `PrincipalLlaveApi` con la cuenta para que `apps.seguridad.alcance` filtre.
- **Una cuenta puede tener varias llaves vivas**: producción y habilitación, o
  la nueva y la vieja durante una rotación. No hay unicidad por cuenta.
- **La cuenta es el único alcance posible.** Un emisor nunca se conecta por su
  lado: quien vaya a emitir directamente se da de alta como su propia cuenta,
  con su emisor y su llave. Por eso la llave no tiene FK a emisor.
- Revocación = `activa = False` (o borrar la fila). Para suspender a un cliente
  concreto sin tocar credenciales: `Emisor.activo = False`, que corta la emisión.
- **`Cuenta.activa = False` corta el acceso de todas sus llaves de golpe** (401),
  sin tener que revocarlas una a una, y sus cuentas dejan de admitir emisores nuevos.

### El mismo NIT puede estar en varias cuentas

`Emisor` es único por `(cuenta, tipo_identificacion, numero_identificacion)`, no
globalmente. El mismo NIT convive en varias integraciones a la vez, y cada fila
lleva **sus propios datos**: correo, software DIAN, certificado y resoluciones
cuelgan del emisor, así que no se mezclan entre cuentas. Dos casos reales:

- **Canales distintos**: facturación por una integración, nómina por otra.
- **Migración de proveedor**: hoy factura con ERP 1 y mañana con ERP 2. Durante
  el traslado convive en las dos, y al terminar el histórico de la primera queda
  intacto en su cuenta.

Lo que **no** puede duplicarse es una resolución de numeración activa. La DIAN
autoriza un solo rango por prefijo: si dos filas del mismo NIT numeraran a la
vez con la misma resolución, cada una llevaría su propio `consecutivo_actual` y
la DIAN rechazaría los repetidos —consecutivos que además ya no se recuperan.

Lo comprueba `resolucion_activa_en_otra_cuenta`
(`apps/emisores/models/resolucion.py`), desde el serializer y desde
`importar-dian`, que persiste sin pasar por él. No es una constraint de base de
datos porque necesita mirar filas de *otro* emisor.

Se mira solo entre las **activas**, y eso es lo que hace posible la migración:
se desactiva la resolución en la cuenta que se deja y queda libre para la nueva.
Un prefijo distinto en cada canal convive sin problema.

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

## Permisos y alcance

- Default `IsAuthenticated` (ya configurado).
- El aislamiento entre inquilinos vive en `apps/seguridad/alcance.py`, que
  responde a una sola pregunta: **¿qué emisores alcanza este solicitante?**

| Solicitante | Alcance |
|---|---|
| Staff de la plataforma | Todos (sin restricción) |
| API Key de cuenta | Todos los emisores de su cuenta |
| Usuario humano (JWT) | Los emisores que tenga **asignados** |

Al **crear** un emisor la pregunta es la otra mitad — *¿de qué cuenta puede
colgarlo?* — y la responde `alcance.cuenta_permitida`: el staff elige la cuenta
(y debe indicarla, no hay default), la integración solo puede usar la suya, y un
usuario humano no da de alta emisores porque no pertenece a ninguna. La cuenta
tiene que estar **activa**.

`AlcanceEmisorMixin` aplica eso en los ViewSets: filtra el queryset en lectura
(lo ajeno responde 404, no 403, para no revelar que existe) y valida el emisor
recibido en escritura (403). Lo usan documentos, adquirientes, emisores,
resoluciones, software y certificados.

### La cuenta es de la llave, no del usuario

**Un usuario no pertenece a ninguna cuenta.** Lo que puede ver son exactamente
los emisores de `Usuario.emisores`, sin techo intermedio: la cuenta no le
concedía permisos, y filtrar por ella dejaría a una persona ver los emisores de
todos los demás clientes de la integración. **Sin emisores asignados no ve
ningún dato** (falla cerrado).

La cuenta sigue donde sí hace falta, en `LlaveApi`: una integración necesita
alcanzar a todos los emisores de su cuenta con una sola credencial. La única
regla que queda sobre los usuarios es que sus emisores sean todos de la misma
cuenta, para que una persona no quede repartida entre integraciones.

### Coherencia de los datos

- Al crear un emisor desde una integración, la cuenta se toma de la credencial
  y se ignora la del cuerpo (`EmisorViewSet.perform_create`).
- Dar de alta un emisor es cosa de la integración o del staff. Un usuario humano
  no tiene cuenta de la que colgarlo, así que recibe **403**.
- Un documento no puede referenciar resolución, adquiriente ni documento de
  referencia de otro emisor (`DocumentoCrearSerializer.validate`).
- El adquiriente pertenece a un emisor y su unicidad es por emisor: el mismo NIT
  puede ser cliente de varios emisores, cada uno con sus propios datos.

## Dependencias

- `djangorestframework-simplejwt==5.5.1`
- `django-cors-headers==4.9.0`

## Mapa de la implementación

| Pieza | Ubicación |
|---|---|
| Modelo `LlaveApi` (+ `generar`, `esta_vigente`, `verificar_secreto`) | `apps/seguridad/models/llave_api.py` |
| Autenticación API Key + `PrincipalLlaveApi` | `apps/seguridad/autenticacion.py` |
| Alcance multi-inquilino (`emisores_permitidos`, `AlcanceEmisorMixin`) | `apps/seguridad/alcance.py` |
| API de gestión de llaves (solo staff) | `apps/seguridad/views/llave_api.py`, ruta `/api/seguridad/llaves-api/` |
| API de usuarios (solo staff) | `apps/seguridad/views/usuario.py`, ruta `/api/seguridad/usuarios/` |
| Alta de llave por CLI | `python manage.py crear_llave_api --cuenta <id> --nombre "..."` |
| Rutas de seguridad (router + JWT) | `apps/seguridad/urls.py` (montado en `/api/seguridad/`) → `usuarios`, `llaves-api`, `token/`, `token/refresh/`, `token/verify/` |
| Auth classes, `SIMPLE_JWT`, CORS | `config/settings/base.py` |
| Variables de entorno | `.env.example` (`CORS_ALLOWED_ORIGINS`, `JWT_ACCESS_MINUTOS`, …) |
| Pruebas de autenticación | `apps/seguridad/tests_autenticacion.py` |
| Pruebas de aislamiento entre inquilinos | `apps/seguridad/tests_alcance.py` |

## Notas

- Sin credenciales la API responde **401** (antes daba 403 con `SessionAuthentication`).
- Se retiraron `SessionMiddleware`, `AuthenticationMiddleware`,
  `django.contrib.sessions` y el `api/auth/` navegable: la API es 100% stateless.
- **Producción**: `DJANGO_SECRET_KEY` debe tener ≥32 caracteres, porque también
  firma los JWT (con una clave corta `pyjwt` emite `InsecureKeyLengthWarning`).

## Pendiente (siguiente iteración)

- **Coste por petición**: `verificar_secreto` usa `check_password` (PBKDF2, ~100 ms)
  y `registrar_uso()` escribe en la BD en *cada* request. Para un secreto
  aleatorio de 40 caracteres el estiramiento de clave no aporta seguridad: basta
  SHA-256 con comparación en tiempo constante. Y `ultimo_uso_en` debería
  actualizarse solo cada N minutos.
- **Throttling**: no hay `DEFAULT_THROTTLE_*`. Conviene limitar por cuenta (no
  por llave) en `enviar/` y `actualizar-estado/`, que pegan contra la DIAN.
- **Trazabilidad**: con una llave compartida por muchos emisores hay que
  registrar `prefijo` + `emisor` en cada emisión; hoy no se guarda.
- **Idempotencia**: `Documento` ya tiene unicidad
  `(emisor, prefijo, consecutivo, documento_tipo)`, que evita duplicar
  consecutivos; falta decidir si se acepta una cabecera `Idempotency-Key` para
  que el reintento del ERP devuelva el mismo recurso en vez de un 400.
- **Emisión asíncrona + webhooks**: `enviar/` llama al WS de la DIAN dentro del
  request; si la DIAN se degrada, el ERP se cuelga y el worker queda ocupado.
