"""Clave de sesión y prueba de dueño para crear el primer administrador.

El PIN antiguo (instance/admin_pin.json, solo hash de werkzeug) ya no protege subidas: ahora
eso lo decide el rol de administrador. Se conserva solo para demostrar que quien crea el
primer administrador es el dueño del servidor. Si no hay PIN, se usa un código de un solo uso
guardado en instance/setup_code.txt (solo legible por el usuario del servicio).
"""

import json
import os
import secrets
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash

INSTANCE_DIR = Path(__file__).resolve().parent / "instance"
PIN_FILE = INSTANCE_DIR / "admin_pin.json"
SECRET_FILE = INSTANCE_DIR / "secret_key.txt"
SETUP_CODE_FILE = INSTANCE_DIR / "setup_code.txt"
MAX_FAILS = 5
LOCK_SECONDS = 10 * 60

_lock = threading.Lock()
_fails = {}


def _chmod(path, mode):
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def harden_instance():
    """instance/ solo para el usuario del servicio (700) y sus archivos 600. Sin efecto en Windows."""
    if not INSTANCE_DIR.exists():
        return
    _chmod(INSTANCE_DIR, 0o700)
    for item in INSTANCE_DIR.iterdir():
        if item.is_file():
            _chmod(item, 0o600)


def _write_private(path, text):
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    _chmod(INSTANCE_DIR, 0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    old_umask = os.umask(0o077) if os.name != "nt" else None
    try:
        tmp.write_text(text, encoding="utf-8")
    finally:
        if old_umask is not None:
            os.umask(old_umask)
    _chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_secret_key():
    """Lee instance/secret_key.txt o lo genera una sola vez."""
    with _lock:
        if SECRET_FILE.exists():
            value = SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        value = secrets.token_hex(32)
        _write_private(SECRET_FILE, value + "\n")
        return value


def read_pin():
    if not PIN_FILE.exists():
        return None
    try:
        data = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("hash"):
        return None
    return data


def is_configured():
    return read_pin() is not None


def setup_code():
    """Código de un solo uso para crear el primer administrador cuando no existe un PIN."""
    with _lock:
        if SETUP_CODE_FILE.exists():
            value = SETUP_CODE_FILE.read_text(encoding="utf-8").strip()
            if len(value) >= 12:
                return value
        value = "-".join(secrets.token_hex(3).upper() for _ in range(3))
        _write_private(SETUP_CODE_FILE, value + "\n")
        return value


def clear_setup_code():
    try:
        SETUP_CODE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def proof_kind():
    return "pin" if is_configured() else "code"


def locked_for(ip):
    with _lock:
        entry = _fails.get(ip)
        if not entry:
            return 0
        remaining = entry["until"] - time.time()
        if remaining > 0:
            return int(remaining) + 1
        if entry["until"]:
            _fails.pop(ip, None)
        return 0


def check_proof(ip, value):
    """Valida el PIN (o el código de configuración). Devuelve (ok, intentos_restantes)."""
    text = str(value or "").strip()
    record = read_pin()
    if record is not None:
        ok = check_password_hash(record["hash"], text)
    else:
        ok = secrets.compare_digest(text.upper(), setup_code().upper())
    with _lock:
        if ok:
            _fails.pop(ip, None)
            return True, MAX_FAILS
        entry = _fails.setdefault(ip, {"count": 0, "until": 0})
        entry["count"] += 1
        left = MAX_FAILS - entry["count"]
        if left <= 0:
            entry["until"] = time.time() + LOCK_SECONDS
            entry["count"] = 0
            left = 0
        return False, left
