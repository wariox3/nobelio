#!/usr/bin/env bash
# Actualiza Nobelio en el servidor: para el servicio, trae el código, migra,
# lo levanta y comprueba que responda.
#   sudo /opt/nobelio/actualizar.sh
set -euo pipefail

cd /opt/nobelio
export DJANGO_SETTINGS_MODULE=config.settings.prod

# Se para antes de tocar nada: así el código viejo no atiende peticiones
# contra una base a medio migrar.
systemctl stop nobelio
# Si algo falla entre el stop y el start, el servicio se quedaría abajo en
# silencio; se levanta igual y el fallo se ve en la salida.
trap 'echo "Falló la actualización; levantando el servicio." >&2; systemctl start nobelio' ERR

git pull
.venv/bin/python manage.py migrate

# Los archivos nuevos los crea root; el servicio los lee por grupo.
chmod -R g+rX /opt/nobelio

systemctl start nobelio
trap - ERR

# Que systemd lance el proceso no significa que la app responda.
# Contra 127.0.0.1 hacen falta las dos cabeceras: sin Host da 400 y sin
# X-Forwarded-Proto, 301.
HOST="$(grep -m1 '^ALLOWED_HOSTS=' .env | cut -d= -f2- | cut -d, -f1 | tr -d " \"'")"
for _ in {1..10}; do
    if curl -fsS --max-time 5 -H "Host: $HOST" -H "X-Forwarded-Proto: https" \
        http://127.0.0.1:8050/estado/ >/dev/null; then
        echo "OK: $(git rev-parse --short HEAD) arriba."
        exit 0
    fi
    sleep 2
done

echo "El servicio no responde." >&2
systemctl status nobelio --no-pager --lines 20 >&2
exit 1
