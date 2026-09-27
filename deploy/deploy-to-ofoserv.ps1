# Desarrollo: este PC, http://127.0.0.1:5050
# Producción: ofoserv, https://spotyofo.ofotech.cl
param(
    [switch]$SkipPush
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

if (-not $SkipPush) {
    git push origin main
}

ssh ofoserv @"
set -euo pipefail
cd /var/www/spotyofo
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt
sudo systemctl restart spotyofo
curl -fsS http://127.0.0.1:5050/api/health
echo
"@
