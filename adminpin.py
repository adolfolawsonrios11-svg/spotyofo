"""PIN local para subir y eliminar canciones. Solo guarda un hash de werkzeug."""

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

INSTANCE_DIR = Path(__file__).resolve().parent / "instance"
PIN_FILE = INSTANCE_DIR / "admin_pin.json"
SECRET_FILE = INSTANCE_DIR / "secret_key.txt"
PIN_PATTERN = re.compile(r"^\d{4,12}$")
MAX_FAILS = 5
LOCK_SECONDS = 10 * 60

_lock = threading.Lock()
_fails = {}


def _write_private(path, text):
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
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
    if not isinstance(data, dict) or not data.get("hash") or not data.get("nonce"):
        return None
    return data


def is_configured():
    return read_pin() is not None


def valid_pin(pin):
    return bool(PIN_PATTERN.match(str(pin or "")))


def save_pin(pin):
    data = {
        "hash": generate_password_hash(str(pin)),
        "nonce": secrets.token_hex(16),
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _write_private(PIN_FILE, json.dumps(data, indent=2) + "\n")
    return data


def create_pin_once(pin):
    """Crea el PIN solo si aún no existe. Devuelve el registro o None si ya había uno."""
    with _lock:
        if read_pin() is not None:
            return None
        return save_pin(pin)


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


def check_pin(ip, pin):
    """Devuelve (ok, registro, intentos_restantes)."""
    record = read_pin()
    if record is None:
        return False, None, MAX_FAILS
    ok = check_password_hash(record["hash"], str(pin or ""))
    with _lock:
        if ok:
            _fails.pop(ip, None)
            return True, record, MAX_FAILS
        entry = _fails.setdefault(ip, {"count": 0, "until": 0})
        entry["count"] += 1
        left = MAX_FAILS - entry["count"]
        if left <= 0:
            entry["until"] = time.time() + LOCK_SECONDS
            entry["count"] = 0
            left = 0
        return False, record, left
