#!/bin/bash
# Instalación de producción en ofoserv. El código ya está en /var/www/spotyofo.
set -euo pipefail

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

sudo python3 - <<'PY'
from pathlib import Path
path = Path("/etc/cloudflared/config.yml")
text = path.read_text(encoding="utf-8")
backup = path.with_name("config.yml.bak.spotyofo")
if not backup.exists():
    backup.write_text(text, encoding="utf-8")
block = "  - hostname: spotyofo.ofotech.cl\n    service: http://127.0.0.1:5050\n"
if "spotyofo.ofotech.cl" not in text:
    catch = "  - service: http_status:404\n"
    if catch not in text:
        raise SystemExit("No está el cierre 404 del túnel.")
    text = text.replace(catch, block + catch, 1)
    path.write_text(text, encoding="utf-8")
    print("ingress-added")
else:
    print("ingress-exists")
PY

TUNNEL="$(sudo awk '/^tunnel:/{print $2; exit}' /etc/cloudflared/config.yml)"
sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
if ! cloudflared tunnel route dns "$TUNNEL" "$HOST_NAME"; then
  echo "No pude crear el DNS. Revisa cert.pem de cloudflared."
  exit 1
fi
sudo systemctl restart cloudflared
echo "SpotyOFO listo en https://${HOST_NAME}"
