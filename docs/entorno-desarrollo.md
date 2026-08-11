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
