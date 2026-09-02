# Desplegar en producción (manual, sin contenedores)

Guía paso a paso para dejar Nobelio sirviendo en `https://api.nobelio.co` desde
un VPS Ubuntu 24.04: PostgreSQL nativo, gunicorn bajo systemd y nginx como
proxy, con certificado de Let's Encrypt vía certbot. La contraparte de
[entorno-desarrollo.md](entorno-desarrollo.md), que cubre la máquina de trabajo.

Sustituye `api.nobelio.co` por tu dominio en todos los comandos.

---

## 0. Antes de empezar

- Un VPS con Ubuntu 24.04 — compruébalo antes de empezar con `lsb_release -ds`.
  En 22.04 no existe `python3.12` en los repos y el proyecto pide 3.12+; si ya
  estás en jammy, o reinstalas o tiras del PPA `deadsnakes`. Referencia: 3 vCPU / 4 GB. Conviene una región
  US‑East: el bucket B2 del proyecto está en `us-east-005`, y por ahí pasan los
  `.p12` y los XML firmados.
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
    postgresql postgresql-contrib git gnupg awscli unattended-upgrades
```

---

## 2. PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE USER nobelio WITH PASSWORD 'clave-larga-y-aleatoria';
CREATE DATABASE nobelio OWNER nobelio;
ALTER ROLE nobelio SET client_encoding TO 'utf8';
ALTER ROLE nobelio SET timezone TO 'America/Bogota';
SQL
```

Por defecto PostgreSQL solo escucha en `localhost`. Déjalo así: la app corre en
la misma máquina y no hay razón para exponer el puerto 5432.

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

Y una segunda clave, la que cifra en la base la clave de los `.p12`. Va aparte
de la anterior a propósito: la `SECRET_KEY` se rota el día que haya que
invalidar los JWT, y eso no puede dejar ilegibles los certificados de todos los
emisores.

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
# /opt/nobelio/.env
DJANGO_SECRET_KEY=<la clave generada>
# Sin esta el proyecto no arranca; ver el aviso al final del apartado.
CERT_ENCRYPTION_KEY=<la clave Fernet generada>
DEBUG=False
ALLOWED_HOSTS=api.nobelio.co
CORS_ALLOWED_ORIGINS=https://app.nobelio.co

# La BD es local; el usuario y la clave son los del paso 2.
DATABASE_URL=postgres://nobelio:clave-larga-y-aleatoria@localhost:5432/nobelio

# 2 = habilitación / Set de Pruebas · 1 = producción
DIAN_ENVIRONMENT=2
DIAN_WSDL_HABILITACION=https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl
DIAN_WSDL_PRODUCCION=https://vpfe.dian.gov.co/WcfDianCustomerServices.svc?wsdl
DIAN_POLICY_ID=https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf
DIAN_POLICY_HASH=<lo calcula apps/dian/firma.calcular_hash_politica()>

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

El archivo lleva la clave de la BD, las de B2, la del respaldo y la de cifrado
de los certificados. Lo lee root para los comandos de gestión y `nobelio` para
correr el servicio; nadie más:

```bash
chown root:nobelio /opt/nobelio/.env
chmod 640 /opt/nobelio/.env
```

> **`CERT_ENCRYPTION_KEY` no tiene valor por defecto: si falta, Django no
> arranca.** Es deliberado —un default silencioso significaría seguir guardando
> las claves de los `.p12` en claro sin que nadie se entere—, pero tiene dos
> consecuencias que conviene tener presentes al desplegar:
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

No hace falta `collectstatic`: la API solo tiene `JSONRenderer`, sin sitio de
administración ni browsable API. No hay estáticos que servir.

---

## 6. gunicorn con systemd

```bash
tee /etc/systemd/system/nobelio.service > /dev/null <<'EOF'
[Unit]
Description=Nobelio — API de facturación electrónica DIAN
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=nobelio
Group=nobelio
WorkingDirectory=/opt/nobelio
Environment=DJANGO_SETTINGS_MODULE=config.settings.prod
ExecStart=/opt/nobelio/.venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8000 \
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

Dos decisiones que importan:

- **`gthread` y no `sync`**: `POST /enviar/` se queda bloqueado esperando a la
  DIAN (`SendBillSync` puede tardar decenas de segundos). Con workers sync, cada
  envío deja un proceso entero inservible mientras tanto.
- **`--timeout 120`**: por encima de lo que la DIAN llega a tardar. Con el
  timeout por defecto (30 s) gunicorn mataría envíos que iban bien.

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
    server_name api.nobelio.co;

    # El .p12 y los PDF son los cuerpos más grandes que pasan por aquí.
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;

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
8000 ya está ocupado por otra aplicación del servidor, cambia **los dos** — es
un fallo silencioso: el dominio sirve la app equivocada sin dar ningún error.

`proxy_set_header X-Forwarded-Proto $scheme` no es opcional:
`config/settings/prod.py` define `SECURE_PROXY_SSL_HEADER` esperando esa
cabecera. Sin ella Django se cree en HTTP y `SECURE_SSL_REDIRECT` entra en un
bucle de redirecciones.

Comprueba que el 80 ya responde antes de pedir el certificado:

```bash
curl -i -H "X-Forwarded-Proto: https" http://api.nobelio.co/estado/
```

### Certificado

```bash
certbot --nginx -d api.nobelio.co
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
api.nobelio.co {
	encode gzip
	request_body { max_size 10MB }
	reverse_proxy 127.0.0.1:8000 {
		header_up X-Forwarded-Proto {scheme}
		transport http { read_timeout 150s }
	}
}
```

---

## 8. Verificar

```bash
curl https://api.nobelio.co/estado/
# → {"servicio": "nobelio", "estado": "ok"}

journalctl -u nobelio -f
```

Contra `127.0.0.1` hacen falta dos cabeceras, y sin ellas parecen fallos del
servicio sin serlo:

```bash
curl -i -H "Host: api.nobelio.co" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8000/estado/
```

- Sin `Host`: **400**. `localhost` no está en `ALLOWED_HOSTS`, y Django compara
  la cadena exacta — `api.nobelio.co` no encaja con `nobelio.co`.
- Sin `X-Forwarded-Proto`: **301** a `https://`. `SECURE_SSL_REDIRECT` está en
  `SecurityMiddleware`, lo primero de la cadena; en el tráfico real esa cabecera
  la pone nginx.

Si el 400 persiste con el Host correcto, revisa el `.env` con
`grep ALLOWED_HOSTS /opt/nobelio/.env | cat -A`: `env.list` no recorta espacios
ni comillas, así que `ALLOWED_HOSTS="api.nobelio.co"` o `a.co, b.co` parsean con
la basura dentro y siguen rechazando. Y el `.env` se lee al importar los
settings: tras editarlo, `systemctl restart nobelio`.

---

## 9. Alta del primer cliente

La cuenta (el tenant) se crea por API con el superusuario —
`CuentaViewSet` exige `IsAdminUser` — porque `crear_llave_api` falla si la
cuenta todavía no existe:

```bash
TOKEN=$(curl -s -X POST https://api.nobelio.co/api/seguridad/token/ \
  -H "Content-Type: application/json" \
  -d '{"correo":"<usuario>","password":"<clave>"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access'])")

curl -X POST https://api.nobelio.co/api/cuentas/cuenta/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nombre":"Cliente Demo SAS","identificacion":"900123456",
       "correo_contacto":"contacto@cliente.co","activa":true}'

cd /opt/nobelio && DJANGO_SETTINGS_MODULE=config.settings.prod \
  .venv/bin/python manage.py crear_llave_api --cuenta <id> --nombre "ERP producción"
```

El secreto de la llave se muestra **una sola vez**. De ahí en adelante, el alta
del emisor sigue [checklist-emision.md](checklist-emision.md).

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

Cuando la DIAN acepte el Set de Pruebas:

1. `DIAN_ENVIRONMENT=1` en el `.env`.
2. `systemctl restart nobelio`.
3. Poner `SoftwareDian.set_pruebas_aceptado` en `True` (hoy no lo marca nada
   solo): es lo que hace que el envío pase de `SendTestSetAsync` a `SendBillSync`.

Repasa también los puntos de
[Pendientes para producción](../README.md#pendientes-para-producción) del README.

---

## Lo que esta guía no cubre

- **Ambiente de habilitación en paralelo** (`pruebas.nobelio.co`): sería repetir
  los pasos 2 a 7 con otra base de datos, otro directorio, otro service de
  systemd escuchando en el 8001 y un segundo `server` en `sites-available`.
  Mantener los dos ambientes separados por host evita el peor error posible:
  emitir contra producción un documento de pruebas.
- **Reconciliación de estados**: cuando la DIAN deja un documento en `enviado`,
  hoy depende de que el ERP llame a `actualizar-estado/`. Falta un comando de
  gestión que recorra los pendientes y un cron que lo dispare.
