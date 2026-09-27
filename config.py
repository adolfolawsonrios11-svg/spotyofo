"""Configuración de SpotyOFO. Los secretos viven en .env, fuera de Git."""

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
