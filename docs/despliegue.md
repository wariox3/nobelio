# Desplegar en producción (manual, sin contenedores)

Guía paso a paso para dejar Nobelio sirviendo en `https://api.rededoc.co` desde
un VPS Ubuntu 24.04: gunicorn bajo systemd y nginx como proxy, con certificado
de Let's Encrypt vía certbot. La base de datos **no** vive en este servidor: es
un PostgreSQL gestionado aparte, y la guía solo lo consume (paso 2). La contraparte de
[entorno-desarrollo.md](entorno-desarrollo.md), que cubre la máquina de trabajo.

Sustituye `api.rededoc.co` por tu dominio en todos los comandos.

---

## 0. Antes de empezar

- Un VPS con Ubuntu 24.04 — compruébalo antes de empezar con `lsb_release -ds`.
  En 22.04 no existe `python3.12` en los repos y el proyecto pide 3.12+; si ya
  estás en jammy, o reinstalas o tiras del PPA `deadsnakes`. Referencia: 3 vCPU / 4 GB. Conviene una región
  US‑East: el bucket B2 del proyecto está en `us-east-005`, y por ahí pasan los
  `.p12` y los XML firmados. La base tampoco está en este servidor (paso 2), así
  que conviene que esté en esa misma región: cada consulta cruza la red.
- El registro DNS **A** del dominio apuntando a la IP del VPS, **resolviendo ya**.
  Sin eso Let's Encrypt no emite el certificado y el paso 7 falla.
- Acceso SSH como root con llave pública. Toda la guía se ejecuta como root:
  el único usuario que creamos es el que corre el servicio, y ése no inicia
  sesión.

---

## 1. Servidor base

El usuario `nobelio` existe para que gunicorn no corra como root, no para
entrar por SSH. Por eso se crea como usuario de sistema: sin contraseña, sin
shell y sin sudo. Si algún día alguien roba la ejecución del proceso, no hereda
una sesión utilizable.

```bash
adduser --system --group --no-create-home --shell /usr/sbin/nologin nobelio
```

La administración sigue siendo root, así que el endurecimiento de
`/etc/ssh/sshd_config` va sobre esa cuenta: llave sí, contraseña no.

```
PermitRootLogin prohibit-password
PasswordAuthentication no
```

Confirma que tu llave esté en `/root/.ssh/authorized_keys` **antes** de recargar
SSH, y deja la sesión actual abierta hasta comprobar que puedes entrar en otra:
con `PasswordAuthentication no` y sin llave válida te quedas fuera del VPS.

```bash
systemctl restart ssh

ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable

apt update && apt install -y python3.12 python3.12-venv python3-pip \
    postgresql-client git gnupg awscli unattended-upgrades
```

`postgresql-client` y no `postgresql`: la base no vive aquí (paso 2). Lo que
hace falta en el servidor es el cliente, para `psql` al diagnosticar y para el
`pg_dump` del paso 10.

---

## 2. PostgreSQL (fuera de este servidor)

**La base no se gestiona en este VPS.** Vive en un servicio aparte, así que aquí
no se instala PostgreSQL ni hay nada que administrar: lo único que sale de este
paso es la cadena de conexión que irá en `DATABASE_URL`.

Lo que hay que pedirle a quien administre la base:

- Una **base de datos vacía** y un **rol dueño de ella**. La app corre sus
  propias migraciones, así que ese rol necesita crear tablas, índices y
  restricciones sobre el esquema; no basta con lectura y escritura sobre tablas
  ya hechas. También es quien crea la tabla `cache_general` del paso 5.
- **Alcance del rol**: dueño de esa base y de nada más. Nada de superusuario.
- El **host, puerto, nombre y credenciales**, y si el servicio exige TLS.

No hace falta pedir `client_encoding` ni `timezone` en el rol, aunque la versión
anterior de esta guía los ponía: con `USE_TZ = True` Django guarda en UTC y fija
la zona de la conexión por su cuenta, y el encoding lo negocia el driver.

La conexión ya no es local, y eso trae tres cosas que antes no existían:

- **Red de salida.** `ufw` solo filtra entrada, así que no hay que abrir nada
  para salir; pero si el proveedor filtra por origen, hay que dar de alta la IP
  del VPS en su lista. Compruébalo antes de migrar:

  ```bash
  psql "$DATABASE_URL" -c "select version();"
  ```

- **TLS.** Si el servicio lo admite —y casi todos los gestionados lo exigen—, va
  en la propia URL. Sin esto las credenciales y los datos fiscales viajan en
  claro entre las dos máquinas:

  ```ini
  DATABASE_URL=postgres://usuario:clave@host-de-la-base:5432/nobelio?sslmode=require
  ```

- **Latencia.** Con la base en la misma máquina cada consulta costaba
  microsegundos; ahora cuesta un ida y vuelta por la red. Conviene que la base
  esté en la misma región que el VPS, y tener presente lo que dice el aviso del
  paso 6 sobre las conexiones.

---

## 3. Código y entorno virtual

```bash
git clone https://github.com/wariox3/nobelio.git /opt/nobelio
cd /opt/nobelio

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

El código queda de root y el servicio solo lo lee: `nobelio` no puede reescribir
la aplicación que ejecuta. Lo único que necesita escribir es `media/`, y solo
como respaldo — con B2 configurado los XML y PDF ni siquiera pasan por el disco.

```bash
mkdir -p /opt/nobelio/media
chown -R root:nobelio /opt/nobelio
chmod -R g+rX /opt/nobelio
chown -R nobelio:nobelio /opt/nobelio/media
```

`gunicorn` ya viene en `requirements.txt`; no hace falta instalarlo aparte.

---

## 4. El archivo `.env`

`config/settings/base.py` lo lee desde la raíz del proyecto. Genera una clave
nueva — **nunca** reutilices la de desarrollo, porque con ella también se firman
los JWT:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Y **dos claves Fernet más**, una para cifrar en la base la clave de los `.p12`
(`CERT_ENCRYPTION_KEY`) y otra para los secretos TOTP del segundo factor
(`MFA_ENCRYPTION_KEY`). Las tres van por separado a propósito: la `SECRET_KEY` se
rota el día que haya que invalidar los JWT, y eso no puede dejar ilegibles ni los
certificados de todos los emisores ni el segundo factor de todo el mundo.

```bash
# Una vez por cada una; no reutilices la misma en las dos.
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
# /opt/nobelio/.env
DJANGO_SECRET_KEY=<la clave generada>
# Sin esta el proyecto no arranca; ver el aviso al final del apartado.
CERT_ENCRYPTION_KEY=<la primera clave Fernet>
# Cifra los secretos TOTP y hashea los códigos de respaldo del segundo factor.
# Tiene default vacío, así que Django arranca sin ella: lo que falla es el MFA,
# y no al desplegar sino la primera vez que alguien lo enrola.
MFA_ENCRYPTION_KEY=<la segunda clave Fernet>
DEBUG=False
ALLOWED_HOSTS=api.rededoc.co

# --- La SPA, que vive en otro dominio ---
CORS_ALLOWED_ORIGINS=https://app.rededoc.co
# Obligatoria: la sesión viaja en cookies httpOnly, y sin credenciales el
# navegador ni las guarda ni las manda. Ver docs/autenticacion.md.
CORS_ALLOW_CREDENTIALS=True
# Dominio registrable compartido por la SPA y la API, para que SameSite=Lax
# deje pasar la cookie entre app.rededoc.co y api.rededoc.co.
AUTH_COOKIE_DOMAIN=.rededoc.co
AUTH_COOKIE_SECURE=True
AUTH_COOKIE_SAMESITE=Lax

# Vida de la sesión. El access es corto porque no se puede revocar antes de que
# venza; SESION_MAXIMA_DIAS es el tope absoluto, tras el cual se vuelve a pasar
# por el login y por el segundo factor aunque se use sin parar.
JWT_ACCESS_MINUTOS=15
JWT_REFRESH_DIAS=1
SESION_MAXIMA_DIAS=30

# --- Páginas del frontend a las que apuntan los correos ---
# HTTPS obligatorio: los dos enlaces llevan un token que abre la cuenta.
URL_VERIFICACION_CORREO=https://app.rededoc.co/verificar-correo
URL_RESTABLECER_CLAVE=https://app.rededoc.co/restablecer-clave

# --- Correo saliente (Zinc) ---
# Por aquí salen la verificación del registro, la recuperación de contraseña,
# los códigos del segundo factor y la notificación de documentos al adquiriente.
ZINC_URL_BASE=https://zinc.semantica.com.co
ZINC_NOMBRE_REMITENTE=RedEDoc

# --- Caché y topes de peticiones ---
# CRÍTICA con varios workers: la de por-proceso da a cada uno su propia cuenta y
# los topes se multiplican por tres. La tabla la crea la migración del paso 5.
# Con la base fuera del servidor, cada comprobación de tope es un viaje por la
# red; si pesa, aquí es donde entra un `redis://host:6379/0`.
CACHE_URL=dbcache://cache_general
# Cuántos proxies hay delante. Con nginx (paso 7) es 1; con nginx + Cloudflare,
# 2. Dejarlo en 0 mete a todo el mundo en el cubo del proxy y los topes por IP
# dejan de proteger nada.
NUM_PROXIES=1

# La BD está fuera de este servidor: host, credenciales y TLS son los que te
# dieron en el paso 2. `sslmode=require` si el servicio lo admite.
DATABASE_URL=postgres://usuario:clave@host-de-la-base:5432/nobelio?sslmode=require

# Ambiente con el que NACE un emisor nuevo (2 = habilitación, 1 = producción).
# No decide contra qué servidor se emite: eso lo dicen los campos del propio
# emisor. Ver el paso 12.
DIAN_ENVIRONMENT=2
DIAN_WSDL_HABILITACION=https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl
DIAN_WSDL_PRODUCCION=https://vpfe.dian.gov.co/WcfDianCustomerServices.svc?wsdl
DIAN_POLICY_ID=https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf
DIAN_POLICY_HASH=<lo calcula apps/dian/firma.calcular_hash_politica()>

# Fabricante del software para la extensión del documento equivalente P.O.S.
# Es quien HIZO el software, igual para todos los emisores de la instalación.
# Tienen default en settings; defínelas solo si la instalación es de otro.
DIAN_FABRICANTE_NOMBRE=Mario A. Estrada
DIAN_FABRICANTE_RAZON_SOCIAL=Semantica Digital S.A.S
DIAN_FABRICANTE_NOMBRE_SOFTWARE=RedEDoc

# Bucket propio de producción, distinto al de desarrollo: aquí caen los .p12
# de clientes reales.
B2_BUCKET=nobelio-produccion
B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
B2_REGION=us-east-005
B2_KEY_ID=<keyID>
B2_APP_KEY=<applicationKey>

# Para el respaldo cifrado del paso 10.
RESPALDO_PASSPHRASE=<clave larga>

# Errores en Sentry. Vacío lo desactiva; sin DSN no se inicializa nada.
SENTRY_DSN=https://<clave>@<organizacion>.ingest.sentry.io/<proyecto>
SENTRY_ENTORNO=produccion
SENTRY_TRACES=0.0
SENTRY_RELEASE=<sha del commit desplegado>
```

Los topes de peticiones (`THROTTLE_*`) se quedan con sus valores por defecto;
están todos listados en `.env.example`, que es la referencia completa de
variables, y se suben sin desplegar código el día que un punto de venta con
muchas cajas se quede corto.

El archivo lleva la clave de la BD, las de B2, la del respaldo y las dos de
cifrado —certificados y segundo factor—. Lo lee root para los comandos de
gestión y `nobelio` para correr el servicio; nadie más:

```bash
chown root:nobelio /opt/nobelio/.env
chmod 640 /opt/nobelio/.env
```

> **Las dos claves Fernet fallan de forma distinta, y esa es la trampa.**
> `CERT_ENCRYPTION_KEY` no tiene default: si falta, Django no arranca y te
> enteras en el acto. `MFA_ENCRYPTION_KEY` sí lo tiene (vacío), así que el
> despliegue parece correcto y revienta con `ImproperlyConfigured` el día que
> alguien enrola el segundo factor. Compruébalas las dos antes de dar por bueno
> el servidor.
>
> Lo que sigue vale para ambas, con la `CERT_ENCRYPTION_KEY` como ejemplo:
>
> - **Guárdala fuera del servidor**, en el mismo sitio donde estén las
>   credenciales de B2. Perderla es perder las claves de todos los certificados:
>   no hay forma de recuperarlas y hay que volver a cargar cada `.p12` con su
>   clave.
> - **No la incluyas en el respaldo del paso 10** junto a la base. El sentido de
>   cifrar la columna es que un volcado por sí solo no sirva; si la clave viaja
>   en el mismo respaldo, vuelven a estar las dos mitades juntas.

> **Sentry.** `SENTRY_DSN` vacío deja la integración apagada, que es lo normal
> fuera de producción. Con DSN se envían las excepciones no controladas y lo que
> se registre a nivel `ERROR`; los rechazos de la DIAN **no** generan alertas,
> solo contexto dentro del evento. Antes de activarlo en un entorno nuevo, lee
> `config/observabilidad.py`: los eventos llevan las variables locales del
> traceback, y lo que impide que salga con ellas la clave del `.p12` es una
> lista de nombres que hay que mantener al día.

---

## 5. Migraciones y datos iniciales

`manage.py` cae por defecto en `config.settings.dev`, así que en el servidor hay
que forzar el módulo de producción en cada comando:

```bash
cd /opt/nobelio
export DJANGO_SETTINGS_MODULE=config.settings.prod

.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py migrate
.venv/bin/python manage.py cargar_catalogos   # las listas .gc de la DIAN
.venv/bin/python manage.py createsuperuser
```

`migrate` crea también la tabla `cache_general` que pide `CACHE_URL`
(`apps/nucleo/migrations/0001_tabla_de_cache.py`): no hace falta
`createcachetable` a mano.

> **El superusuario recién creado no puede iniciar sesión.** `create_superuser`
> no marca `is_verified`, y `POST /token/` responde **403 "Tienes que confirmar
> tu correo antes de iniciar sesión."** antes de mirar nada más. Como el correo
> de verificación solo lo manda el registro público, al primero hay que marcarlo
> a mano:
>
> ```bash
> .venv/bin/python manage.py shell -c "
> from django.contrib.auth import get_user_model
> get_user_model().objects.filter(email='<tu correo>').update(is_verified=True)
> "
> ```

No hace falta `collectstatic`: la API solo tiene `JSONRenderer`, sin sitio de
administración ni browsable API. No hay estáticos que servir.

---

## 6. gunicorn con systemd

```bash
tee /etc/systemd/system/nobelio.service > /dev/null <<'EOF'
[Unit]
Description=Nobelio — API de facturación electrónica DIAN
After=network-online.target
Wants=network-online.target

[Service]
User=nobelio
Group=nobelio
WorkingDirectory=/opt/nobelio
Environment=DJANGO_SETTINGS_MODULE=config.settings.prod
ExecStart=/opt/nobelio/.venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8005 \
    --worker-class gthread \
    --workers 3 \
    --threads 4 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/nobelio/media

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now nobelio
systemctl status nobelio
```

Tres decisiones que importan:

- **`gthread` y no `sync`**: `POST /enviar/` se queda bloqueado esperando a la
  DIAN (`SendBillSync` puede tardar decenas de segundos). Con workers sync, cada
  envío deja un proceso entero inservible mientras tanto.
- **`--timeout 120`**: por encima de lo que la DIAN llega a tardar. Con el
  timeout por defecto (30 s) gunicorn mataría envíos que iban bien.
- **Sin dependencia de la base**: sin `postgresql.service` local, systemd no
  tiene forma de esperar a que la base remota esté lista. Lo cubre
  `Restart=always`: si la base no responde al arrancar, el servicio reintenta
  cada 5 segundos en vez de quedarse abajo.

> **Las conexiones ahora cruzan la red.** Django abre y cierra una conexión por
> petición (`CONN_MAX_AGE` no está definido, y su default es `0`). Con la base en
> `localhost` eso no se notaba; contra una base remota son un TCP y un handshake
> TLS por cada petición, sumados a la latencia de todas las consultas. Si el
> tiempo de respuesta se resiente, lo que hay que mirar es reutilizar conexiones
> con `CONN_MAX_AGE`, teniendo en cuenta cuántas admite el plan contratado:
> `--workers 3 × --threads 4` son hasta 12 conexiones vivas por servidor.

`User=nobelio` es la cuenta de sistema del paso 1; systemd no necesita que
tenga shell para lanzar el proceso. Y como el resto de `/opt/nobelio` queda
fuera de `ReadWritePaths`, el servicio no puede tocar su propio código ni el
`.env`.

Solo `DJANGO_SETTINGS_MODULE` va en el unit. El resto lo lee `django-environ` del
`.env`; y como las variables reales del entorno tienen prioridad sobre ese
archivo, no hay conflicto entre ambas vías.

---

## 7. nginx como proxy

```bash
apt install -y nginx certbot python3-certbot-nginx
```

El sitio se escribe primero **solo en HTTP**: certbot necesita el puerto 80 para
resolver el reto de Let's Encrypt, y es él quien añade después el bloque TLS.

```bash
tee /etc/nginx/sites-available/nobelio > /dev/null <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name api.rededoc.co;

    # El .p12 y los PDF son los cuerpos más grandes que pasan por aquí.
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8005;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Por encima del timeout de gunicorn (120 s), para que el que corte sea él.
        proxy_read_timeout 150s;
        proxy_connect_timeout 10s;
    }
}
EOF

ln -s /etc/nginx/sites-available/nobelio /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

`proxy_pass` tiene que apuntar al mismo puerto del `--bind` del paso 6. Si el
8005 ya está ocupado por otra aplicación del servidor, cambia **los tres**
(aquí, el `--bind` del paso 6 y el chequeo final de `actualizar.sh`) — es
un fallo silencioso: el dominio sirve la app equivocada sin dar ningún error.

`proxy_set_header X-Forwarded-Proto $scheme` no es opcional:
`config/settings/prod.py` define `SECURE_PROXY_SSL_HEADER` esperando esa
cabecera. Sin ella Django se cree en HTTP y `SECURE_SSL_REDIRECT` entra en un
bucle de redirecciones.

Comprueba que el 80 ya responde antes de pedir el certificado:

```bash
curl -i -H "X-Forwarded-Proto: https" http://api.rededoc.co/estado/
```

### Certificado

```bash
certbot --nginx -d api.rededoc.co
```

Certbot reescribe el archivo del sitio: añade `listen 443 ssl`, las rutas del
certificado y —si se lo pides— el redirect del 80 al 443. La renovación queda en
un timer de systemd:

```bash
systemctl list-timers | grep certbot
certbot renew --dry-run
```

Si falla la emisión, casi siempre es una de dos: el registro A todavía no
resuelve, o el 80 está cerrado en `ufw`. `journalctl -u nginx -n 50` y el propio
mensaje de certbot lo dicen.

### Detrás de Cloudflare

Si el dominio está en Cloudflare, deja el registro A en **DNS only** (nube gris)
hasta que certbot termine: con el proxy activo Cloudflare termina el TLS por su
cuenta y el reto HTTP-01 no llega al origen.

Ya con el certificado emitido puedes pasarlo a **Proxied**, y entonces en
*SSL/TLS → Overview* hay que elegir **Full (strict)**. Con *Flexible*,
Cloudflare habla HTTP con el origen, Django ve una petición insegura y
`SECURE_SSL_REDIRECT` devuelve un 301 que Cloudflare vuelve a convertir en HTTP:
bucle infinito.

### Con Caddy

La alternativa, si prefieres no gestionar certificados a mano — Caddy los pide y
los renueva solo:

```
api.rededoc.co {
	encode gzip
	request_body { max_size 10MB }
	reverse_proxy 127.0.0.1:8005 {
		header_up X-Forwarded-Proto {scheme}
		transport http { read_timeout 150s }
	}
}
```

---

## 8. Verificar

```bash
curl https://api.rededoc.co/estado/
# → {"servicio": "nobelio", "estado": "ok"}

journalctl -u nobelio -f
```

Contra `127.0.0.1` hacen falta dos cabeceras, y sin ellas parecen fallos del
servicio sin serlo:

```bash
curl -i -H "Host: api.rededoc.co" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8005/estado/
```

- Sin `Host`: **400**. `localhost` no está en `ALLOWED_HOSTS`, y Django compara
  la cadena exacta — `api.rededoc.co` no encaja con `rededoc.co`.
- Sin `X-Forwarded-Proto`: **301** a `https://`. `SECURE_SSL_REDIRECT` está en
  `SecurityMiddleware`, lo primero de la cadena; en el tráfico real esa cabecera
  la pone nginx.

Si el 400 persiste con el Host correcto, revisa el `.env` con
`grep ALLOWED_HOSTS /opt/nobelio/.env | cat -A`: `env.list` no recorta espacios
ni comillas, así que `ALLOWED_HOSTS="api.rededoc.co"` o `a.co, b.co` parsean con
la basura dentro y siguen rechazando. Y el `.env` se lee al importar los
settings: tras editarlo, `systemctl restart nobelio`.

---

## 9. Alta del primer cliente

No hay tenant que crear: el cliente **es un usuario**, y los emisores que
alcanza son los que estén a su nombre o asignados a él
(`apps.seguridad.alcance.emisores_permitidos`). Se da de alta por el registro
público, que es anónimo:

```bash
curl -X POST https://api.rededoc.co/api/seguridad/registro/ \
  -H "Content-Type: application/json" \
  -d '{"email":"contacto@cliente.co","password":"<clave larga>",
       "nombre_corto":"Cliente Demo SAS"}'
```

Eso manda el correo de verificación a `URL_VERIFICACION_CORREO`; el enlace lleva
el token a `POST /api/seguridad/registro/verificar/`, y hasta que no se confirme,
el login responde 403. Comprueba de paso que Zinc esté entregando: si el correo
no sale, el alta queda a medias sin decir nada.

Ya verificado, el cliente entra por la SPA. Para probar el ingreso desde la
consola hacen falta cookies, porque **la sesión no viaja en el cuerpo**: `token/`
deja `access_token` y `refresh_token` como cookies `httpOnly` y la respuesta solo
trae los datos del usuario.

```bash
curl -s -c galletas.txt -X POST https://api.rededoc.co/api/seguridad/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"contacto@cliente.co","password":"<clave larga>"}'

# Con las cookies guardadas, cualquier ruta autenticada:
curl -s -b galletas.txt https://api.rededoc.co/api/seguridad/me/
```

Si la cuenta tiene segundo factor, `token/` no entrega sesión: responde
`mfa_requerido` y un `mfa_token` que resuelve `POST /api/seguridad/token/mfa/`.

Para el ERP, que no es un navegador, la vía es una llave de API. Va ligada a un
**usuario** y alcanza exactamente lo mismo que él:

```bash
cd /opt/nobelio && DJANGO_SETTINGS_MODULE=config.settings.prod \
  .venv/bin/python manage.py crear_llave_api \
    --usuario contacto@cliente.co --nombre "ERP producción"
```

El secreto se muestra **una sola vez**, y se manda como
`Authorization: Api-Key <prefijo>.<secreto>`. No existe autenticación por
`Bearer`: las únicas dos clases son la llave de API y el JWT leído de la cookie
(`REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`).

De ahí en adelante, el alta del emisor sigue
[checklist-emision.md](checklist-emision.md).

---

## 10. Respaldos

Los documentos electrónicos tienen obligación de conservación (5 años), así que
esto no es opcional. `/opt/nobelio/respaldo.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/nobelio && set -a && source .env && set +a

ARCHIVO="/tmp/nobelio-$(date +%Y%m%d-%H%M%S).sql.gz.gpg"

# El volcado no toca el disco sin cifrar: va por tubería hasta gpg.
pg_dump "$DATABASE_URL" | gzip -9 \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase "$RESPALDO_PASSPHRASE" --output "$ARCHIVO"

aws s3 cp "$ARCHIVO" "s3://${B2_BUCKET}/respaldos/$(basename "$ARCHIVO")" \
  --endpoint-url "$B2_ENDPOINT_URL"

rm -f "$ARCHIVO"
```

`pg_dump` sale de `postgresql-client` (paso 1) y ataca la base remota por la
misma `DATABASE_URL` que usa la app, TLS incluido. Un detalle que muerde: el
cliente tiene que ser de versión **igual o mayor** que el servidor, o aborta con
`server version mismatch` y el respaldo no se hace —sin que nadie se entere,
porque el cron escribe en un log que nadie mira—. Compruébalo el día del
despliegue y cada vez que el proveedor suba la versión de la base:

```bash
pg_dump --version
psql "$DATABASE_URL" -tAc "show server_version;"
```

El script lee el `.env` y produce un volcado completo de la base, así que es de
root y solo root lo ejecuta:

```bash
chown root:root /opt/nobelio/respaldo.sh
chmod 700 /opt/nobelio/respaldo.sh
crontab -e   # el de root
# 0 3 * * * /opt/nobelio/respaldo.sh >> /var/log/nobelio-respaldo.log 2>&1
```

Restaura un respaldo al menos una vez contra una BD de prueba: uno que nunca se
restauró no es un respaldo.

---

## 11. Actualizar la aplicación

El repositorio trae `actualizar.sh`, que hace esto mismo —parando el servicio
mientras migra— y comprueba al final que `/estado/` responda:

```bash
sudo /opt/nobelio/actualizar.sh
```

A mano:

```bash
cd /opt/nobelio
export DJANGO_SETTINGS_MODULE=config.settings.prod
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate

# Los archivos nuevos los crea root; el servicio los lee por grupo.
chmod -R g+rX /opt/nobelio
systemctl restart nobelio
```

---

## 12. Paso a producción ante la DIAN

**El paso a producción es por emisor, no del despliegue.** Contra qué servidor
de la DIAN sale cada documento lo deciden `ambiente_facturacion`,
`ambiente_nomina` y `ambiente_documento_equivalente` **del emisor**, y el
documento se lleva el valor al crearse y lo sella al firmar. `DIAN_ENVIRONMENT`
del `.env` solo fija con cuál nace un emisor nuevo: cambiarlo a `1` no mueve a
nadie que ya esté dado de alta. Son tres habilitaciones independientes, así que
el mismo emisor puede facturar en producción y seguir en habilitación para
nómina.

Cuando la DIAN acepte el Set de Pruebas de una operación, el sistema lo detecta
solo: `_marcar_habilitacion_superada` (`apps/dian/servicios.py`) pone
`SoftwareDian.set_pruebas_aceptado` —que es lo que hace pasar el envío de
`SendTestSetAsync` a `SendBillSync`— y marca la bandera `habilitado_*` que
corresponda. No hay que tocar ninguna de las dos a mano.

Lo que sí es una decisión tuya es mover el ambiente del emisor, y solo se puede
después de esa habilitación:

```bash
curl -X PATCH https://api.rededoc.co/api/emisores/emisor/<id>/ \
  -H "Authorization: Api-Key <prefijo>.<secreto>" \
  -H "Content-Type: application/json" \
  -d '{"ambiente_facturacion": 1}'
```

Las tres banderas `habilitado_*` son de **solo lectura** en la API: constatan un
hecho que declara la DIAN, no una decisión del cliente, y como además condicionan
el paso a producción de nómina y documento equivalente, poder escribirlas era
poder saltarse la habilitación entera. Si la DIAN habilita por fuera del
automatismo, se marcan por backend:

```bash
cd /opt/nobelio && DJANGO_SETTINGS_MODULE=config.settings.prod \
  .venv/bin/python manage.py shell -c "
from apps.emisores.models import Emisor
Emisor.objects.filter(numero_identificacion='900123456').update(habilitado_nomina=True)
"
```

Repasa también los puntos de
[Pendientes para producción](../README.md#pendientes-para-producción) del README.

---

## Lo que esta guía no cubre

- **Ambiente de habilitación en paralelo** (`pruebas.rededoc.co`): sería repetir
  los pasos 2 a 7 con otra base de datos, otro directorio, otro service de
  systemd escuchando en el 8001 y un segundo `server` en `sites-available`.
  Mantener los dos ambientes separados por host evita el peor error posible:
  emitir contra producción un documento de pruebas.
- **Reconciliación de estados**: cuando la DIAN deja un documento en `enviado`,
  hoy depende de que el ERP llame a `actualizar-estado/`. Falta un comando de
  gestión que recorra los pendientes y un cron que lo dispare.
