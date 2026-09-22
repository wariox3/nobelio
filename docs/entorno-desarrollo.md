# Montar el entorno de desarrollo desde cero

Guía paso a paso para dejar Nobelio corriendo en una máquina nueva (Linux/macOS).
El [README](../README.md) asume que Python, PostgreSQL y el `.env` ya existen;
esta guía cubre justo lo anterior.

---

## 1. Requisitos del sistema

- **Python 3.12+**
- **PostgreSQL** (servidor corriendo localmente, o accesible por red)

En Debian/Ubuntu:

```bash
sudo apt install python3-venv postgresql postgresql-client
```

---

## 2. Entorno virtual e instalación

Puedes crear el venv dentro del proyecto (como indica el README) o en
`~/.venvs/<nombre>` si prefieres mantenerlo fuera del repo — en ese caso
ajusta `python.defaultInterpreterPath` en `.vscode/settings.json`.

```bash
cd nobelio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Crear el rol y la base de datos en PostgreSQL

Nobelio **no crea el rol ni la base de datos por ti** — `DATABASE_URL` es
obligatorio y Django falla al arrancar si la base no existe.

Con el usuario `postgres` del sistema (requiere `sudo`):

```bash
sudo -u postgres psql -c "CREATE ROLE mi_usuario WITH LOGIN PASSWORD 'mi_clave';"
sudo -u postgres psql -c "CREATE DATABASE bdnobelio OWNER mi_usuario;"
```

Verifica la conexión:

```bash
PGPASSWORD=mi_clave psql -h localhost -U mi_usuario -d bdnobelio -c '\conninfo'
```

> Si usas el rol `postgres` en vez de uno propio, en Debian/Ubuntu por defecto
> la autenticación local es por `peer`, no por contraseña — necesitas cambiar
> `pg_hba.conf` a `md5`/`scram-sha-256` para conexiones TCP, o simplemente crear
> un rol propio como arriba (más simple y es lo recomendado).

---

## 4. Configurar `.env`

```bash
cp .env.example .env
```

Como mínimo, ajusta:

- `DJANGO_SECRET_KEY` — cualquier cadena aleatoria larga en dev.
- `DATABASE_URL` — con el rol/clave/BD del paso 3, ej.:
  `postgres://mi_usuario:mi_clave@localhost:5432/bdnobelio`

Las variables de **Backblaze B2** (`B2_*`) son opcionales en desarrollo: si
quedan vacías, el almacenamiento de archivos (XML, certificados `.p12`, PDF)
cae automáticamente a disco local (`MEDIA_ROOT`). Solo son obligatorias en
producción y para que la API acepte subir certificados `.p12`.

Las variables `DIAN_*` ya traen valores por defecto razonables para
habilitación (`DIAN_ENVIRONMENT=2`) — no hace falta tocarlas para desarrollar
localmente.

Las de **Celery** deciden qué pasa al crear un documento (ver el paso 6.1):

- `CELERY_BROKER_URL` — el RabbitMQ de desarrollo. Una instancia propia, nunca la
  de producción ni la de torio.
- `CELERY_TASK_ALWAYS_EAGER=True` — para trabajar **sin RabbitMQ**: las tareas
  corren en el acto, dentro de la petición.
- `DOCUMENTOS_EMITIR_AL_CREAR` — encendida (el valor por defecto), **crear un
  documento lo firma y lo envía a la DIAN de habilitación**, con broker o en modo
  eager. Si solo quieres crear documentos para probar la API, ponla en `False`: se
  quedan en `borrador` y se emiten con `emitir/` cuando tú decidas.

---

## 5. Migraciones, catálogos y usuario staff

```bash
python manage.py migrate
python manage.py cargar_catalogos     # tipos, tributos, municipios, monedas...
python manage.py createsuperuser      # staff: da de alta cuentas, usuarios y llaves
```

---

## 6. Levantar el servidor

```bash
python manage.py runserver
```

- API: http://localhost:8000/api/
- Estado: http://localhost:8000/estado/

> No hay `/admin/`: `django.contrib.admin` no está instalado y la API es
> *stateless* (sin sesiones). Todo se hace por la API o por comandos.

### 6.1 El worker de Celery

Crear un documento encola su emisión, y al aceptarse la DIAN se encola el aviso a
los webhooks. Con broker, eso lo corre el worker, en otra terminal:

```bash
.venv/bin/celery -A config worker -l info -Q emitir_documento,avisos_webhook,celery \
    --without-gossip --without-mingle --without-heartbeat
```

Los tres `--without-*` quitan el tráfico de control entre workers: con uno solo
no sirve, y en CloudAMQP cuenta contra los mensajes del plan, también en la
instancia de desarrollo. Son los mismos que lleva la unidad del servidor.

Sin el worker, los documentos se quedan en `borrador` esperando en la cola. Tras
cambiar código de una tarea hay que reiniciarlo: no recarga solo como `runserver`.

Sin RabbitMQ, `CELERY_TASK_ALWAYS_EAGER=True` en el `.env` y no hace falta worker.
La suite de pruebas no necesita ninguno de los dos: corre las tareas en el acto y
no emite al crear (`config/settings/test.py`).

Sigue con el flujo completo de uso de la API en el
[README](../README.md#flujo-de-uso-completo-api) o el
[checklist de emisión](checklist-emision.md).

---

## 7. Pruebas

La suite de tests **no usa Backblaze B2** ni tu base de datos de desarrollo:
`manage.py test` selecciona automáticamente `config.settings.test`, que
desactiva B2 (cae a almacenamiento local temporal) y crea/destruye una base de
datos de pruebas aparte sobre la misma conexión de `DATABASE_URL`.

```bash
python manage.py test              # toda la suite
python manage.py test apps.dian    # solo el núcleo DIAN
```

El rol de `DATABASE_URL` necesita permiso para crear bases de datos (lo tiene
por defecto si lo creaste como en el paso 3, ya que es owner de su propia BD;
si no, `ALTER ROLE mi_usuario CREATEDB;`).
