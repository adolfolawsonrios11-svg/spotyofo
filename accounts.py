"""Cuentas de SpotyOFO: registro con aprobación del administrador, contraseñas y bloqueos.

- Las contraseñas se guardan solo como hash (werkzeug, scrypt con sal).
- Registro propio -> cuenta «pending». Solo un administrador la aprueba.
- Bloqueo por cuenta (5 intentos fallidos -> 15 min) y por IP (en memoria, 1 worker).
- session_version: al cambiar la contraseña, bloquear o borrar, las sesiones viejas dejan de valer.
- Cuenta demo (is_demo = 1): sin bloqueo por cuenta (solo por IP), para que nadie la deje
  bloqueada a propósito. Sus restricciones están en app.py y su reinicio en demo.py.
- claim_legacy(): pasa los datos anteriores a las cuentas (listas propias, Me gusta,
  historial) al primer administrador. Una sola vez (marcado en app_meta).
"""

import re
import secrets
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 128
ACCOUNT_MAX_FAILS = 5
ACCOUNT_LOCK_MINUTES = 15
IP_MAX_FAILS = 20
IP_WINDOW = 15 * 60
REGISTER_PER_IP = 5
REGISTER_WINDOW = 60 * 60
HASH_METHOD = "scrypt"
COMMON = {
    "1234567890", "12345678910", "0123456789", "contraseña", "contrasena1", "password12",
    "password123", "qwertyuiop", "spotyofo123", "1111111111", "aaaaaaaaaa", "abcdefghij",
}
STATUS_LABEL = {
    "pending": "Pendiente",
    "active": "Activa",
    "rejected": "Rechazada",
    "blocked": "Bloqueada",
}
USER_FIELDS = (
    "id, username, display_name, role, status, must_change_password, session_version, "
    "failed_logins, locked_until, created_at, approved_at, approved_by, last_login_at, is_demo"
)


class AccountError(Exception):
    def __init__(self, message, code=400, field=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def hash_password(password):
    return generate_password_hash(str(password), method=HASH_METHOD)


# Hash de relleno: si el usuario no existe igual se calcula un hash (mismo tiempo de respuesta).
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class RateLimiter:
    """Ventana deslizante en memoria. Suficiente con un solo worker de gunicorn."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits = {}

    def _prune(self, key, window, now):
        stamps = [stamp for stamp in self._hits.get(key, []) if now - stamp < window]
        if stamps:
            self._hits[key] = stamps
        else:
            self._hits.pop(key, None)
        return stamps

    def count(self, key, window):
        with self._lock:
            return len(self._prune(key, window, time.time()))

    def hit(self, key, window):
        with self._lock:
            now = time.time()
            stamps = self._prune(key, window, now)
            stamps.append(now)
            self._hits[key] = stamps
            if len(self._hits) > 5000:
                for old in list(self._hits)[:1000]:
                    self._hits.pop(old, None)
            return len(stamps)

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def wait(self, key, window):
        with self._lock:
            stamps = self._prune(key, window, time.time())
            return int(window - (time.time() - stamps[0])) + 1 if stamps else 0


limiter = RateLimiter()


def normalize_username(raw):
    return str(raw or "").strip().lower()


def validate_username(username):
    if not USERNAME_RE.match(username):
        raise AccountError(
            "El usuario debe tener de 3 a 32 caracteres: letras minúsculas, números, punto, guion o guion bajo.",
            field="username",
        )
    return username


def clean_display_name(raw, fallback):
    text = " ".join(str(raw or "").split())[:60]
    return text or fallback


def validate_password(password, username=""):
    text = str(password or "")
    if len(text) < MIN_PASSWORD:
        raise AccountError(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.", field="password")
    if len(text) > MAX_PASSWORD:
        raise AccountError(f"La contraseña admite hasta {MAX_PASSWORD} caracteres.", field="password")
    lowered = text.lower()
    if lowered in COMMON or len(set(text)) < 4:
        raise AccountError("Esa contraseña es muy fácil de adivinar. Usa una frase o mezcla palabras.", field="password")
    if username and username in lowered:
        raise AccountError("La contraseña no puede contener tu nombre de usuario.", field="password")
    return text


def public_user(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"],
        "status": row["status"],
        "status_label": STATUS_LABEL.get(row["status"], row["status"]),
        "must_change_password": bool(row["must_change_password"]),
        "created_at": _fmt(row.get("created_at")),
        "approved_at": _fmt(row.get("approved_at")),
        "last_login_at": _fmt(row.get("last_login_at")),
        "locked": bool(row.get("locked_until")) and _is_future(row.get("locked_until")),
        "demo": bool(row.get("is_demo")),
    }


def _fmt(value):
    return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else value


def _is_future(value):
    from datetime import datetime

    return hasattr(value, "timestamp") and value > datetime.now()


# ---------- consultas ----------

_admin_seen = False


def admin_exists(conn):
    """¿Hay al menos un administrador activo? (se recuerda en memoria una vez que existe)."""
    global _admin_seen
    if _admin_seen:
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE role = 'admin' AND status = 'active' LIMIT 1")
        found = cur.fetchone() is not None
    _admin_seen = found
    return found


def forget_admin_cache():
    global _admin_seen
    _admin_seen = False


def get_user(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {USER_FIELDS} FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()


def pending_count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE status = 'pending'")
        return int(cur.fetchone()["n"])


def list_users(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {USER_FIELDS},
                   (SELECT COUNT(*) FROM playlists p WHERE p.owner_id = u.id AND p.editorial = 0) AS playlists,
                   (SELECT COUNT(*) FROM user_likes l WHERE l.user_id = u.id) AS likes
            FROM users u
            ORDER BY FIELD(status, 'pending', 'active', 'blocked', 'rejected'), created_at DESC, id DESC
            """
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        item = public_user(row)
        item["playlists"] = int(row["playlists"])
        item["likes"] = int(row["likes"])
        result.append(item)
    return result


# ---------- alta ----------

def create_user(conn, username, display_name, password, role="user", status="pending", approved_by=None):
    username = validate_username(normalize_username(username))
    password = validate_password(password, username)
    display = clean_display_name(display_name, username)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            raise AccountError("Ese nombre de usuario ya está en uso.", 409, field="username")
        try:
            cur.execute(
                """
                INSERT INTO users (username, display_name, password_hash, role, status, approved_at, approved_by)
                VALUES (%s, %s, %s, %s, %s, IF(%s = 'active', NOW(), NULL), %s)
                """,
                (username, display, hash_password(password), role, status, status, approved_by),
            )
        except Exception as exc:  # carrera con el índice único
            if "Duplicate" in str(exc):
                raise AccountError("Ese nombre de usuario ya está en uso.", 409, field="username") from exc
            raise
        return cur.lastrowid


def claim_legacy(cur, admin_id):
    """Datos de antes de las cuentas -> primer administrador. Idempotente."""
    cur.execute("SELECT meta_value FROM app_meta WHERE meta_key = 'legacy_owner' FOR UPDATE")
    row = cur.fetchone()
    cur.execute("UPDATE playlists SET owner_id = %s WHERE editorial = 0 AND owner_id IS NULL", (admin_id,))
    lists = cur.rowcount
    cur.execute("UPDATE play_history SET user_id = %s WHERE user_id IS NULL", (admin_id,))
    history = cur.rowcount
    likes = device_likes = 0
    if row is None:
        cur.execute(
            "INSERT IGNORE INTO user_likes (user_id, track_id, liked_at) SELECT %s, track_id, liked_at FROM liked_tracks",
            (admin_id,),
        )
        likes = cur.rowcount
        cur.execute(
            """
            INSERT IGNORE INTO user_device_likes (user_id, track_id, liked_at)
            SELECT %s, track_id, liked_at FROM device_likes
            """,
            (admin_id,),
        )
        device_likes = cur.rowcount
        cur.execute(
            "INSERT INTO app_meta (meta_key, meta_value) VALUES ('legacy_owner', %s)",
            (str(admin_id),),
        )
    return {"playlists": lists, "history": history, "likes": likes, "device_likes": device_likes}


def create_first_admin(conn, username, display_name, password, claim=True):
    """Crea el administrador principal solo si todavía no hay uno. Devuelve (id, migrados)."""
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('spotyofo_setup', 10) AS ok")
        if not cur.fetchone()["ok"]:
            raise AccountError("Otra configuración está en curso. Intenta de nuevo.", 409)
    try:
        forget_admin_cache()
        if admin_exists(conn):
            raise AccountError("Ya existe un administrador. Inicia sesión.", 409)
        conn.begin()
        try:
            user_id = create_user(conn, username, display_name, password, role="admin", status="active")
            with conn.cursor() as cur:
                moved = claim_legacy(cur, user_id) if claim else {}
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return user_id, moved
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK('spotyofo_setup')")


# ---------- ingreso ----------

def ip_wait(ip):
    if limiter.count(("login-ip", ip), IP_WINDOW) >= IP_MAX_FAILS:
        return limiter.wait(("login-ip", ip), IP_WINDOW)
    return 0


def authenticate(conn, username, password, ip):
    """Devuelve la fila del usuario activo o lanza AccountError con un mensaje seguro."""
    wait = ip_wait(ip)
    if wait:
        raise AccountError(f"Demasiados intentos desde esta red. Espera {max(1, wait // 60)} min.", 429)
    username = normalize_username(username)
    text = str(password or "")[:MAX_PASSWORD]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {USER_FIELDS}, password_hash, (locked_until IS NOT NULL AND locked_until > NOW()) AS is_locked "
            "FROM users WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()
    if row is None:
        check_password_hash(_DUMMY_HASH, text)
        limiter.hit(("login-ip", ip), IP_WINDOW)
        raise AccountError("Usuario o contraseña incorrectos.", 401)
    demo = bool(row["is_demo"])
    if row["is_locked"] and not demo:
        limiter.hit(("login-ip", ip), IP_WINDOW)
        raise AccountError(
            f"Cuenta bloqueada por intentos fallidos. Espera {ACCOUNT_LOCK_MINUTES} minutos o pide al administrador que restablezca tu contraseña.",
            429,
        )
    if not check_password_hash(row["password_hash"], text):
        limiter.hit(("login-ip", ip), IP_WINDOW)
        if demo:
            # Cuenta demo compartida: solo cuenta el límite por IP; no se bloquea la cuenta.
            raise AccountError("Usuario o contraseña incorrectos.", 401)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET locked_until = IF(failed_logins + 1 >= %s, NOW() + INTERVAL %s MINUTE, locked_until),
                    failed_logins = failed_logins + 1
                WHERE id = %s
                """,
                (ACCOUNT_MAX_FAILS, ACCOUNT_LOCK_MINUTES, row["id"]),
            )
            cur.execute("UPDATE users SET failed_logins = 0 WHERE id = %s AND locked_until > NOW()", (row["id"],))
        raise AccountError("Usuario o contraseña incorrectos.", 401)
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = %s", (row["id"],))
    status = row["status"]
    if status == "pending":
        raise AccountError("Tu cuenta está esperando la aprobación del administrador.", 403)
    if status == "rejected":
        raise AccountError("Tu solicitud de acceso fue rechazada.", 403)
    if status == "blocked":
        raise AccountError("Tu cuenta está bloqueada. Habla con el administrador.", 403)
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (row["id"],))
    row.pop("password_hash", None)
    return row


def verify_password(conn, user_id, password):
    with conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return bool(row) and check_password_hash(row["password_hash"], str(password or "")[:MAX_PASSWORD])


def set_password(conn, user_id, password, username="", must_change=False):
    password = validate_password(password, username)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET password_hash = %s, must_change_password = %s, session_version = session_version + 1,
                failed_logins = 0, locked_until = NULL
            WHERE id = %s
            """,
            (hash_password(password), 1 if must_change else 0, user_id),
        )
        cur.execute("SELECT session_version FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return row["session_version"] if row else None


def temporary_password():
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.isdigit() for c in value) and any(c.islower() for c in value) and any(c.isupper() for c in value):
            return value


# ---------- administración ----------

def _active_admins(cur):
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'active'")
    return int(cur.fetchone()["n"])


def admin_action(conn, actor, target_id, action):
    """approve | reject | block | unblock | reset-password. Devuelve (usuario, extra)."""
    target = get_user(conn, target_id)
    if not target:
        raise AccountError("Usuario no encontrado.", 404)
    if target["id"] == actor["id"] and action in ("reject", "block", "reset-password"):
        raise AccountError("No puedes hacer eso con tu propia cuenta. Usa «Cambiar contraseña».", 400)
    if target.get("is_demo") and action == "reset-password":
        raise AccountError("La contraseña de la cuenta demo se define en el servidor (SPOTY_DEMO_PASSWORD).", 409)
    extra = {}
    with conn.cursor() as cur:
        if action == "approve":
            if target["status"] not in ("pending", "rejected"):
                raise AccountError("Solo se aprueban cuentas pendientes o rechazadas.", 409)
            cur.execute(
                "UPDATE users SET status = 'active', approved_at = NOW(), approved_by = %s WHERE id = %s",
                (actor["id"], target["id"]),
            )
        elif action == "reject":
            if target["status"] != "pending":
                raise AccountError("Solo se rechazan cuentas pendientes.", 409)
            cur.execute(
                "UPDATE users SET status = 'rejected', session_version = session_version + 1 WHERE id = %s",
                (target["id"],),
            )
        elif action == "block":
            if target["status"] != "active":
                raise AccountError("Solo se bloquean cuentas activas.", 409)
            if target["role"] == "admin" and _active_admins(cur) <= 1:
                raise AccountError("No se puede bloquear al último administrador.", 409)
            cur.execute(
                "UPDATE users SET status = 'blocked', session_version = session_version + 1 WHERE id = %s",
                (target["id"],),
            )
        elif action == "unblock":
            if target["status"] != "blocked":
                raise AccountError("La cuenta no está bloqueada.", 409)
            cur.execute(
                "UPDATE users SET status = 'active', failed_logins = 0, locked_until = NULL WHERE id = %s",
                (target["id"],),
            )
        elif action == "reset-password":
            temp = temporary_password()
            set_password(conn, target["id"], temp, must_change=True)
            extra["temporary_password"] = temp
        else:
            raise AccountError("Acción no válida.", 400)
    return public_user(get_user(conn, target["id"])), extra


def delete_user(conn, actor, target_id):
    """Borra la cuenta, sus listas, Me gusta e historial. Devuelve las portadas a borrar."""
    target = get_user(conn, target_id)
    if not target:
        raise AccountError("Usuario no encontrado.", 404)
    if target["id"] == actor["id"]:
        raise AccountError("No puedes borrar tu propia cuenta.", 400)
    conn.begin()
    try:
        with conn.cursor() as cur:
            if target["role"] == "admin" and target["status"] == "active" and _active_admins(cur) <= 1:
                raise AccountError("No se puede borrar al último administrador.", 409)
            cur.execute("SELECT id, cover_file FROM playlists WHERE owner_id = %s AND editorial = 0", (target["id"],))
            lists = cur.fetchall()
            covers = [row["cover_file"] for row in lists if row["cover_file"] == f"user-{row['id']}.svg"]
            cur.execute("DELETE FROM playlists WHERE owner_id = %s AND editorial = 0", (target["id"],))
            cur.execute("DELETE FROM play_history WHERE user_id = %s", (target["id"],))
            cur.execute("DELETE FROM users WHERE id = %s", (target["id"],))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return covers
