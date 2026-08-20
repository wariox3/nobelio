# Desplegar en producción (manual, sin contenedores)

Guía paso a paso para dejar Nobelio sirviendo en `https://api.nobelio.co` desde
un VPS Ubuntu 24.04: PostgreSQL nativo, gunicorn bajo systemd y Caddy como
proxy con HTTPS automático. La contraparte de
[entorno-desarrollo.md](entorno-desarrollo.md), que cubre la máquina de trabajo.

Sustituye `api.nobelio.co` por tu dominio en todos los comandos.

---

## 0. Antes de empezar

- Un VPS con Ubuntu 24.04. Referencia: 3 vCPU / 4 GB. Conviene una región
  US‑East: el bucket B2 del proyecto está en `us-east-005`, y por ahí pasan los
  `.p12` y los XML firmados.
- El registro DNS **A** del dominio apuntando a la IP del VPS, **resolviendo ya**.
  Sin eso Let's Encrypt no emite el certificado y el paso 7 falla.
- Acceso SSH como root para el primer arranque.

---

## 1. Servidor base

```bash
# como root, la primera vez
adduser nobelio && usermod -aG sudo nobelio
```

Copia tu llave SSH al nuevo usuario y endurece `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
```

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
sudo mkdir -p /opt/nobelio && sudo chown nobelio:nobelio /opt/nobelio
git clone https://github.com/wariox3/nobelio.git /opt/nobelio
cd /opt/nobelio

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
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

```ini
# /opt/nobelio/.env
DJANGO_SECRET_KEY=<la clave generada>
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
```

```bash
chmod 600 /opt/nobelio/.env
```

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
sudo tee /etc/systemd/system/nobelio.service > /dev/null <<'EOF'
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
ReadWritePaths=/opt/nobelio

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now nobelio
sudo systemctl status nobelio
```

Dos decisiones que importan:

- **`gthread` y no `sync`**: `POST /enviar/` se queda bloqueado esperando a la
  DIAN (`SendBillSync` puede tardar decenas de segundos). Con workers sync, cada
  envío deja un proceso entero inservible mientras tanto.
- **`--timeout 120`**: por encima de lo que la DIAN llega a tardar. Con el
  timeout por defecto (30 s) gunicorn mataría envíos que iban bien.

Solo `DJANGO_SETTINGS_MODULE` va en el unit. El resto lo lee `django-environ` del
`.env`; y como las variables reales del entorno tienen prioridad sobre ese
archivo, no hay conflicto entre ambas vías.

---

## 7. Caddy como proxy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
api.nobelio.co {
	encode gzip

	# El .p12 y los PDF son los cuerpos más grandes que pasan por aquí.
	request_body {
		max_size 10MB
	}

	reverse_proxy 127.0.0.1:8000 {
		header_up X-Forwarded-Proto {scheme}

		# Por encima del timeout de gunicorn, para que el que corte sea él.
		transport http {
			read_timeout 150s
		}
	}
}
EOF

sudo systemctl reload caddy
```

`header_up X-Forwarded-Proto` no es opcional: `config/settings/prod.py` define
`SECURE_PROXY_SSL_HEADER` esperando esa cabecera. Sin ella Django se cree en
HTTP y `SECURE_SSL_REDIRECT` entra en un bucle de redirecciones.

Con nginx es lo mismo más `certbot --nginx`, `proxy_set_header X-Forwarded-Proto
$scheme;` y `proxy_read_timeout 150s;`.

---

## 8. Verificar

```bash
curl https://api.nobelio.co/estado/
# → {"servicio": "nobelio", "estado": "ok"}

sudo journalctl -u nobelio -f
```

Si pruebas contra `127.0.0.1` directamente vas a recibir un **400**, y no es un
fallo del servicio: `localhost` no está en `ALLOWED_HOSTS`. Hay que mandar el
Host explícito:

```bash
curl -H "Host: api.nobelio.co" http://127.0.0.1:8000/estado/
```

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

```bash
chmod +x /opt/nobelio/respaldo.sh
crontab -e
# 0 3 * * * /opt/nobelio/respaldo.sh >> /var/log/nobelio-respaldo.log 2>&1
```

Restaura un respaldo al menos una vez contra una BD de prueba: uno que nunca se
restauró no es un respaldo.

---

## 11. Actualizar la aplicación

```bash
cd /opt/nobelio
export DJANGO_SETTINGS_MODULE=config.settings.prod
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
sudo systemctl restart nobelio
```

---

## 12. Paso a producción ante la DIAN

Cuando la DIAN acepte el Set de Pruebas:

1. `DIAN_ENVIRONMENT=1` en el `.env`.
2. `sudo systemctl restart nobelio`.
3. Confirmar que `SoftwareDian.set_pruebas_aceptado` esté en `True`: es lo que
   hace que el envío pase de `SendTestSetAsync` a `SendBillSync`.

Repasa también los puntos de
[Pendientes para producción](../README.md#pendientes-para-producción) del README.

---

## Lo que esta guía no cubre

- **Ambiente de habilitación en paralelo** (`pruebas.nobelio.co`): sería repetir
  los pasos 2 a 7 con otra base de datos, otro directorio, otro service de
  systemd escuchando en el 8001 y un segundo bloque de sitio en el `Caddyfile`.
  Mantener los dos ambientes separados por host evita el peor error posible:
  emitir contra producción un documento de pruebas.
- **Reconciliación de estados**: cuando la DIAN deja un documento en `enviado`,
  hoy depende de que el ERP llame a `actualizar-estado/`. Falta un comando de
  gestión que recorra los pendientes y un cron que lo dispare.
