# Arquitectura de autenticación

Estado: **implementado**. La API Key es de 2026-06-24; el alcance por usuario y
la sesión en cookies con segundo factor son posteriores y cambiaron parte de lo
que decía este documento.

El servicio tiene dos tipos de clientes con necesidades opuestas, así que usa
**dos mecanismos de autenticación que coexisten** en DRF. Cada petición se
autentica con lo que traiga: una cabecera o una cookie.

| Cliente | Naturaleza | Mecanismo |
|---|---|---|
| **ERP** | Máquina ↔ máquina, sin humano, larga duración | **API Key** en cabecera, ligada a un usuario |
| **Navegador** | Front en otro dominio (`rededoc.co` → `api.rededoc.co`), personas | **Sesión en cookies `httpOnly`** (JWT dentro) |

Regla de oro: **la API Key nunca viaja al navegador; la cookie de sesión nunca
se usa en el ERP.**

```python
# config/settings/base.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.seguridad.autenticacion.LlaveApiAuthentication",  # ERP
        "apps.seguridad.autenticacion.JwtDeCookie",             # navegador
    ],
    ...
}
```

> **No existe `Authorization: Bearer`.** El JWT del navegador viaja en una cookie
> `httpOnly` y de ahí lo lee `JwtDeCookie`; `JWTAuthentication` de simplejwt no
> está en la lista. Mandar un `Bearer` a esta API devuelve 401, y un cliente
> escrito contra esa suposición no funciona.

> No hay sesión de Django: se retiraron `SessionAuthentication`,
> `SessionMiddleware` y `django.contrib.sessions`, y no hay `api/auth/`
> navegable. Que la sesión viaje en cookies no la hace *stateful*: la cookie
> lleva el JWT, y el servidor no guarda nada salvo la lista negra de refresh
> anulados.

---

## 1. ERP → API Key (ligada a un usuario)

La credencial cuelga de un **usuario** y actúa en su nombre: alcanza exactamente
los mismos emisores que él, ni uno más. No cuelga de un emisor porque una
integración como RedDoc ERP factura para muchos, y una llave por emisor no
aportaría nada —las N llaves vivirían igualmente en el mismo servidor, así que
el radio de daño ante un compromiso sería el mismo y el coste de provisionar,
rotar y revocar N secretos se multiplicaría—.

```
LlaveApi
  usuario      FK -> seguridad.Usuario   # actúa en su nombre: su mismo alcance
  nombre       str        # "RedDoc producción", etc.
  prefijo      str(8)     # identificador público, para buscar la fila
  clave_hash   str        # hash del secreto; el secreto NUNCA se guarda en claro
  activa       bool
  expira_en    datetime (null, opcional)
  ultimo_uso_en datetime (null)
```

- Cabecera: `Authorization: Api-Key <prefijo>.<secreto>`
- El secreto se muestra **una sola vez** al crearla; luego solo queda el hash.
- La clase de autenticación busca por `prefijo`, verifica el hash y deja un
  `PrincipalLlaveApi` con el usuario, para que `apps.seguridad.alcance` filtre.
- **Un usuario puede tener varias llaves vivas**: producción y habilitación, o la
  nueva y la vieja durante una rotación. No hay unicidad.
- Revocación = `activa = False` (o borrar la fila). Para suspender a un cliente
  concreto sin tocar credenciales: `Emisor.activo = False`, que corta la emisión.
- Desactivar al usuario (`is_active = False`) corta de golpe todas sus llaves.

Se crean por CLI o por su propio endpoint:

```bash
python manage.py crear_llave_api --usuario ana@empresa.co --nombre "ERP producción"
```

### El NIT es único en toda la plataforma

Un emisor existe una sola vez: la unicidad es `(tipo_identificacion,
numero_identificacion)`, sin nada que la acote.

La consecuencia a tener presente: quien dé de alta un NIT ajeno impide que su
dueño real lo registre. No puede **emitir** —para eso hace falta el `.p12` de
ese NIT—, pero sí ocupar el sitio. El mensaje de error no dice de quién es, para
no filtrar quién usa la plataforma.

## 2. Navegador → sesión en cookies `httpOnly`

Nada de esto viaja en el cuerpo de la respuesta: `emitir()`
(`apps/seguridad/sesion.py`) escribe `access_token` y `refresh_token` como
cookies `httpOnly`, que el JavaScript de la página no puede leer. Un XSS ya no se
lleva la sesión, y el front no tiene que decidir dónde guardarla.

El precio es que **toda petición desde el navegador necesita credenciales**:
`fetch(..., { credentials: "include" })` o `axios.defaults.withCredentials = true`.

### Las rutas

Todo cuelga de `/api/seguridad/`. El detalle exacto de cada una está en
`/api/docs/` (ver [el esquema OpenAPI](../README.md#documentación-de-la-api));
aquí solo el mapa:

| Ruta | Qué hace |
|---|---|
| `POST registro/` | Alta pública. Manda el correo de confirmación |
| `POST registro/verificar/` | Confirma el correo con el token del enlace |
| `POST registro/reenviar/` | Otro enlace de confirmación |
| `POST token/` | Ingreso con email y contraseña |
| `POST token/mfa/` | Resuelve el segundo paso |
| `POST token/mfa/reenviar/` | Otro código del segundo factor |
| `POST token/refresh/` | Renueva la sesión. **Sin cuerpo**: lee la cookie |
| `POST token/cerrar/` | Cierra la sesión y borra las cookies |
| `POST token/recuperar/` | Manda el enlace para restablecer la contraseña |
| `POST token/restablecer/` | Fija la contraseña nueva |
| `GET me/` | Quién es quien pregunta |
| `mfa/…` | Gestión del segundo factor sobre la propia cuenta |

No hay `token/verify/`: con el token en una cookie que el front no lee, no hay
nada que verificar por su cuenta.

### El ingreso puede terminar en dos sitios

`token/` **no siempre entrega la sesión**. Si la cuenta tiene segundo factor
—y el dispositivo no está recordado—, responde `mfa_requerido: true` con un
`mfa_token`, y quien emite la sesión es `token/mfa/`. El front tiene que mirar
ese campo antes que nada.

Es la única razón por la que el segundo factor sirve de algo: si el primer paso
ya entregara cookies, el segundo sería decorativo.

Sin correo confirmado, el ingreso responde **403** antes de mirar nada más.

### Vida de la sesión

- **Access de 15 minutos** (`JWT_ACCESS_MINUTOS`): es lo único que viaja en cada
  petición y no se puede revocar antes de que venza.
- **Refresh de 1 día** (`JWT_REFRESH_DIAS`), que **rota en cada uso**: el
  anterior queda en la lista negra, así que un token robado deja de servir en
  cuanto el dueño legítimo refresca.
- **Tope absoluto de 30 días** (`SESION_MAXIMA_DIAS`), en el claim propio `ses`.
  Sin él, rotar a diario haría que una sesión no caducara nunca y el segundo
  factor, que solo se verifica al ingresar, dejaría de significar nada.

### CORS y el dominio de la cookie

`django-cors-headers` ya está instalado y configurado. Con la sesión en cookies,
**`CORS_ALLOW_CREDENTIALS` no es opcional**: sin él el navegador ni las guarda ni
las manda.

```ini
CORS_ALLOWED_ORIGINS=https://rededoc.co
CORS_ALLOW_CREDENTIALS=True
AUTH_COOKIE_DOMAIN=.rededoc.co
AUTH_COOKIE_SAMESITE=Lax
AUTH_COOKIE_SECURE=True
```

`SameSite=Lax` es lo que frena el CSRF —el navegador no manda la cookie en
escrituras que vengan de otro sitio— y exige que el front y la API compartan
dominio registrable. Si algún día viven en dominios distintos hay que bajar a
`SameSite=None`, y entonces esa protección se pierde.

Con comodín no vale: la especificación prohíbe `Access-Control-Allow-Origin: *`
junto con credenciales, así que los orígenes se enumeran uno a uno.

---

## Permisos y alcance

- Default `IsAuthenticated` (ya configurado).
- El aislamiento entre inquilinos vive en `apps/seguridad/alcance.py`, que
  responde a una sola pregunta: **¿qué emisores alcanza este solicitante?**

| Solicitante | Alcance |
|---|---|
| Staff de la plataforma | Todos (sin restricción) |
| API Key | Lo mismo que la persona dueña de la llave |
| Persona | Los emisores **suyos** más los que le hayan **asignado** |

Las dos vías de una persona se suman en `emisores_permitidos`: los emisores que
dio de alta (`Emisor.usuario`) y los que otro le compartió (`Usuario.emisores`).
**Sin ninguna de las dos no ve ningún dato** — falla cerrado.

Al **crear** un emisor no hay nada que elegir: queda a nombre de quien lo da de
alta, y con una API Key a nombre de la persona dueña de la llave, porque la
llave actúa por ella y no por sí misma. El campo `usuario` es de solo lectura en
el serializer, así que el cuerpo no puede ponerlo a nombre de un tercero
(`EmisorViewSet.perform_create`). Al editar tampoco se mueve: transferir un
emisor merecería su propia acción y su propia comprobación.

`AlcanceEmisorMixin` aplica eso en los ViewSets: filtra el queryset en lectura
(lo ajeno responde 404, no 403, para no revelar que existe) y valida el emisor
recibido en escritura (403). Lo usan documentos, nómina, emisores, resoluciones,
software y certificados.

### No hay cuenta ni inquilino intermedio

Hubo un modelo `Cuenta` que agrupaba usuarios y emisores, y se retiró. Lo que
hay hoy es más simple y no tiene punto ciego: **la unidad es la persona**, y los
emisores cuelgan de ella. Compartir el acceso a un emisor es añadir a alguien a
`Usuario.emisores`, con la granularidad suficiente para que un contador vea un
solo emisor de los varios de un cliente.

Merece decirse porque el modelo anterior dejaba una trampa: filtrar por cuenta
habría dejado a cualquier persona de una integración ver los emisores de todos
los demás clientes de esa integración.

### Coherencia de los datos

- El dueño de un emisor lo pone la vista con quien hace la petición; el cuerpo
  no lo decide (`EmisorViewSet.perform_create`).
- Las tres banderas `habilitado_*` del emisor son de **solo lectura**: constatan
  un hecho que declara la DIAN y condicionan el paso a producción, así que
  poder escribirlas era poder saltarse la habilitación.
- Un documento no puede referenciar resolución ni documento de referencia de
  otro emisor (`DocumentoCrearSerializer.validate`). La resolución se busca por
  su número dentro del emisor del documento, así que un número ajeno responde
  igual que uno inexistente.
- El adquiriente no se referencia: sus datos llegan en cada documento y se
  guardan pegados a él, de modo que no hay cartera de clientes que pueda
  cruzarse entre emisores.

## Dependencias

- `djangorestframework-simplejwt==5.5.1` — los JWT que van dentro de la cookie
- `django-cors-headers==4.9.0`
- `pyotp==2.9.0` — TOTP del segundo factor

## Mapa de la implementación

| Pieza | Ubicación |
|---|---|
| Modelo `LlaveApi` (+ `generar`, `esta_vigente`, `verificar_secreto`) | `apps/seguridad/models/llave_api.py` |
| Autenticación API Key + `PrincipalLlaveApi` + `JwtDeCookie` | `apps/seguridad/autenticacion.py` |
| Emisión y cierre de la sesión en cookies | `apps/seguridad/sesion.py` |
| Segundo factor (TOTP, desafíos, códigos de respaldo, dispositivos) | `apps/seguridad/mfa.py`, `apps/seguridad/models/mfa.py` |
| Alta pública y verificación del correo | `apps/seguridad/verificacion.py`, `views/registro.py` |
| Recuperación de contraseña | `apps/seguridad/recuperacion.py`, `views/recuperacion.py` |
| Alcance multi-inquilino (`emisores_permitidos`, `AlcanceEmisorMixin`) | `apps/seguridad/alcance.py` |
| Topes de peticiones por credencial, IP y destinatario | `apps/seguridad/limites.py` |
| API de gestión de llaves (cada quien las suyas; el staff, todas) | `apps/seguridad/views/llave_api.py`, ruta `/api/seguridad/llave-api/` |
| API de usuarios (solo staff) | `apps/seguridad/views/usuario.py`, ruta `/api/seguridad/usuario/` |
| Alta de llave por CLI | `python manage.py crear_llave_api --usuario <correo> --nombre "..."` |
| Rutas de seguridad | `apps/seguridad/urls.py`, montado en `/api/seguridad/` |
| Auth classes, `SIMPLE_JWT`, cookies, CORS, topes | `config/settings/base.py` |
| Cómo se describen las dos autenticaciones en OpenAPI | `apps/nucleo/esquema.py` |
| Variables de entorno | `.env.example` (`CORS_*`, `AUTH_COOKIE_*`, `JWT_*`, `MFA_ENCRYPTION_KEY`, `THROTTLE_*`) |
| Pruebas de autenticación | `apps/seguridad/tests_autenticacion.py` |
| Pruebas de aislamiento entre inquilinos | `apps/seguridad/tests_alcance.py` |
| Pruebas de registro, recuperación y topes públicos | `apps/seguridad/tests_registro.py`, `tests_recuperacion.py`, `tests_limites_publicos.py` |

## Notas

- Sin credenciales la API responde **401** (antes daba 403 con `SessionAuthentication`).
- **Producción**: `DJANGO_SECRET_KEY` debe tener ≥32 caracteres, porque también
  firma los JWT (con una clave corta `pyjwt` emite `InsecureKeyLengthWarning`).
- **`NUM_PROXIES` tiene que coincidir con los proxies reales.** Los topes por IP
  leen `X-Forwarded-For`, y con el valor equivocado o se saltan —mandando una IP
  falsa en cada petición— o meten a todo el mundo en el mismo cubo. Con nginx es
  1; con Cloudflare delante, 2.
- Los contadores de los topes viven en la caché, así que en producción tiene que
  ser compartida (`CACHE_URL`): con la de por-proceso, cada worker lleva su
  propia cuenta y los topes se multiplican por el número de workers.

## Pendiente (siguiente iteración)

- **Coste por petición**: `verificar_secreto` sigue usando `check_password`
  (PBKDF2, ~100 ms) en cada petición del ERP. Para un secreto aleatorio de 40
  caracteres el estiramiento de clave no aporta seguridad: basta SHA-256 con
  comparación en tiempo constante. Lo de `ultimo_uso_en` ya está resuelto —
  `registrar_uso()` solo escribe cada `INTERVALO_REGISTRO_USO`—.
- **Trazabilidad**: con una llave que alcanza muchos emisores, la traza de
  emisión debería registrar también el `prefijo` de la llave que la disparó;
  hoy no se guarda.
- **Idempotencia**: `Documento` ya tiene unicidad
  `(emisor, prefijo, consecutivo, documento_tipo)`, que evita duplicar
  consecutivos; falta decidir si se acepta una cabecera `Idempotency-Key` para
  que el reintento del ERP devuelva el mismo recurso en vez de un 400.
- **Emisión asíncrona + webhooks**: `enviar/` llama al WS de la DIAN dentro del
  request; si la DIAN se degrada, el ERP se cuelga y el worker queda ocupado.
