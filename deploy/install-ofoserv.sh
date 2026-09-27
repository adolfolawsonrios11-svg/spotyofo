#!/bin/bash
# Instalación de producción en ofoserv. El código ya está en /var/www/spotyofo.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE=/etc/spotyofo.env
HOST_NAME=spotyofo.ofotech.cl
cd "$ROOT"

sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-pip

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [[ ! -f "$ENV_FILE" ]]; then
  PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sudo tee "$ENV_FILE" >/dev/null <<EOF
SPOTY_ENV=production
SPOTY_DB_HOST=127.0.0.1
SPOTY_DB_PORT=3306
SPOTY_DB_USER=spotyofo
SPOTY_DB_PASSWORD=${PASS}
SPOTY_DB_NAME=spotyofo
SPOTY_HOST=127.0.0.1
SPOTY_PORT=5050
SPOTY_TRUST_PROXY=1
EOF
  sudo chown root:ofo "$ENV_FILE"
  sudo chmod 640 "$ENV_FILE"
  sudo mysql <<SQL
CREATE DATABASE IF NOT EXISTS spotyofo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'spotyofo'@'127.0.0.1' IDENTIFIED BY '${PASS}';
GRANT ALL PRIVILEGES ON spotyofo.* TO 'spotyofo'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
  unset PASS
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
.venv/bin/python synthesize.py

sudo cp "$ROOT/deploy/spotyofo.service" /etc/systemd/system/spotyofo.service
sudo systemctl daemon-reload
sudo systemctl enable --now spotyofo
sudo systemctl restart spotyofo
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:5050/api/health; then
    echo
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:5050/api/health >/dev/null
echo "SpotyOFO en https://${HOST_NAME} (túnel propio cloudflared-spotyofo, sin tocar el túnel compartido)"
