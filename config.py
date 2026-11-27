"""Configuración de OFO Music. Los secretos viven en .env, fuera de Git."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_env(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


_load_env(ROOT / ".env")


def _need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta {name}. Crea .env a partir de .env.example.")
    return value


ENV = os.environ.get("SPOTY_ENV", "local").strip() or "local"
DB_CONFIG = {
    "host": os.environ.get("SPOTY_DB_HOST", "127.0.0.1").strip() or "127.0.0.1",
    "port": int(os.environ.get("SPOTY_DB_PORT", "3306")),
    "user": _need("SPOTY_DB_USER"),
    "password": _need("SPOTY_DB_PASSWORD"),
    "database": _need("SPOTY_DB_NAME"),
    "charset": "utf8mb4",
}
HOST = os.environ.get("SPOTY_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("SPOTY_PORT", "5050"))
TRUST_PROXY = os.environ.get("SPOTY_TRUST_PROXY", "0").strip() == "1"

# Nombre nuevo (OFO Music) con dos direcciones. La app no tiene el host fijo: responde en el que le llegue
# (cookies sin Domain, CSP 'self', sin CORS, Origin comparado con request.host).
#   SPOTY_CANONICAL_HOST   dirección nueva, sin esquema (p. ej. music.ofotech.cl). Los enlaces compartidos nuevos nacen con ella.
#   SPOTY_LEGACY_HOSTS     direcciones antiguas separadas por coma (p. ej. spotyofo.ofotech.cl).
#   SPOTY_LEGACY_REDIRECT  1 = las páginas (GET/HEAD) de un host antiguo responden 301 al canónico. Apagado por defecto.
CANONICAL_HOST = os.environ.get("SPOTY_CANONICAL_HOST", "").strip().lower()
CANONICAL_SCHEME = (os.environ.get("SPOTY_CANONICAL_SCHEME", "https").strip().lower() or "https")
LEGACY_HOSTS = frozenset(item.strip().lower() for item in os.environ.get("SPOTY_LEGACY_HOSTS", "").split(",") if item.strip())
LEGACY_REDIRECT = os.environ.get("SPOTY_LEGACY_REDIRECT", "0").strip() == "1" and bool(CANONICAL_HOST) and bool(LEGACY_HOSTS)
