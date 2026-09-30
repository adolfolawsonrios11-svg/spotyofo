"""SpotyOFO: reproductor con cuentas propias aprobadas por el administrador."""

import gzip
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import accounts
import adminpin
import demo
import devicelibrary
import genres
import localfiles
import playlistedit
import uploads
from config import HOST, PORT, TRUST_PROXY
from db import TRACK_SQL, connect, init_schema, row_dict
from songs import song_key

log = logging.getLogger("spotyofo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

COOKIE_SECURE = TRUST_PROXY or os.environ.get("SPOTY_COOKIE_SECURE", "0").strip() == "1"
REGISTRATION_OPEN = os.environ.get("SPOTY_REGISTRATION", "1").strip() != "0"

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
# 200 MB por archivo + margen para el sobre multipart. Se sube un archivo por petición.
app.config["MAX_CONTENT_LENGTH"] = uploads.MAX_FILE_BYTES + 2 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = COOKIE_SECURE
app.config["SESSION_COOKIE_NAME"] = "spotyofo_session"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.secret_key = adminpin.load_secret_key()
adminpin.harden_instance()
if TRUST_PROXY:
    # Detrás de cloudflared (127.0.0.1): IP real del visitante y esquema https.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["PREFERRED_URL_SCHEME"] = "https"
uploads.ensure_dirs()
uploads.purge_pending()

ROOT_DIR = Path(__file__).resolve().parent


def compute_asset_version():
    """Versión de los archivos estáticos: cambia sola cuando se edita JS, CSS o el service worker."""
    newest = 0
    for pattern in ("static/js/*.js", "static/css/*.css", "pwa/*"):
        for path in ROOT_DIR.glob(pattern):
            newest = max(newest, int(path.stat().st_mtime))
    return format(newest, "x")


ASSET_VERSION = compute_asset_version()

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
PALETTE = playlistedit.PALETTE
write_playlist_cover = playlistedit.write_playlist_cover


# ---------- seguridad: rutas públicas, cabeceras y CSRF ----------

# Todo lo demás exige sesión (páginas, API, audio y portadas).
PUBLIC_EXACT = {"/api/health", "/manifest.webmanifest", "/sw.js", "/favicon.ico", "/robots.txt"}
PUBLIC_PREFIX = ("/static/css/", "/static/js/", "/static/icons/", "/static/favicon.svg")
AUTH_PAGES = {"/login", "/login/demo", "/register", "/setup", "/logout"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def get_db():
    if "db" not in g:
        g.db = connect()
        init_schema(g.db)
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def fail(message, code=400, **extra):
    return jsonify(error=message, **extra), code


def is_api():
    return request.path.startswith("/api/")


def client_ip():
    return request.remote_addr or "?"


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def same_origin():
    """Bloquea peticiones de otros sitios: cabecera propia y, si viene, Origin igual al host."""
    if request.headers.get("X-Requested-With") != "SpotyOFO":
        return False
    return origin_ok()


def origin_ok():
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return False
    site = request.headers.get("Sec-Fetch-Site")
    if site and site not in ("same-origin", "none"):
        return False
    return True


def csrf_ok():
    """API: cabecera X-CSRF-Token igual al token de la sesión + mismo origen."""
    sent = request.headers.get("X-CSRF-Token", "")
    token = session.get("csrf", "")
    return bool(token) and same_origin() and hmac.compare_digest(sent, token)


def form_csrf_ok():
    """Formularios HTML (login, registro...): campo oculto + mismo origen."""
    sent = str(request.form.get("csrf_token", ""))
    token = session.get("csrf", "")
    return bool(token) and origin_ok() and hmac.compare_digest(sent, token)


def safe_next(value):
    text = str(value or "").strip()[:300]
    if not text.startswith("/") or text.startswith("//") or text.startswith("/\\"):
        return "/"
    if text.rstrip("?").split("?")[0] in AUTH_PAGES or text.startswith("/api/") or text.startswith("/static/"):
        return "/"
    return text.rstrip("?") or "/"


def no_store_redirect(location):
    response = redirect(location)
    response.headers["Cache-Control"] = "no-store"
    return response


def load_user():
    user_id = session.get("uid")
    if not user_id:
        return None
    user = accounts.get_user(get_db(), user_id)
    if not user or user["status"] != "active" or session.get("ver") != user["session_version"]:
        session.clear()
        return None
    g.user = user
    return user


def login_user(user):
    """Sesión nueva en cada ingreso (evita fijación de sesión)."""
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["ver"] = user["session_version"]
    session["csrf"] = secrets.token_urlsafe(32)
    session["at"] = int(time.time())


def uid():
    return g.user["id"]


def is_demo():
    return bool(g.get("user")) and bool(g.user.get("is_demo"))


def is_admin():
    # La cuenta demo nunca tiene permisos de administrador, aunque alguien le cambie el rol.
    return bool(g.get("user")) and g.user["role"] == "admin" and not is_demo()


DEMO_FORBIDDEN = "La cuenta demo no puede hacer esto."
DEMO_LOGIN_PER_IP = 30
DEMO_LOGIN_WINDOW = 15 * 60


@app.before_request
def gate():
    g.user = None
    path = request.path
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
        return None
    db = get_db()
    load_user()
    if path in AUTH_PAGES:
        return None
    if not accounts.admin_exists(db):
        if is_api():
            return fail("Primero hay que crear la cuenta de administrador.", 401, needs="setup")
        if path.startswith("/static/"):
            return app.response_class("Inicia sesión.", 401, mimetype="text/plain")
        return no_store_redirect("/setup")
    if g.user is None:
        if is_api():
            return fail("Tu sesión terminó. Vuelve a iniciar sesión.", 401, needs="login")
        if path.startswith("/static/"):
            return app.response_class("Inicia sesión.", 401, mimetype="text/plain")
        target = safe_next(request.full_path)
        return no_store_redirect("/login" if target == "/" else "/login?next=" + quote(target, safe="/"))
    if g.user["must_change_password"] and path != "/account/password":
        if is_api():
            return fail("Debes cambiar tu contraseña temporal.", 403, needs="password")
        if not path.startswith("/static/"):
            return no_store_redirect("/account/password")
    if request.method not in SAFE_METHODS and is_api() and not csrf_ok():
        return fail("Petición rechazada. Recarga la página e intenta de nuevo.", 403, needs="reload")
    return None


@app.after_request
def security_headers(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Content-Security-Policy", CSP)
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    if request.is_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    path = request.path
    status = response.status_code
    if path in ("/api/equipo/audio", "/api/equipo/image") and status in (200, 206, 304):
        headers["Cache-Control"] = "private, max-age=3600"
    elif is_api():
        headers["Cache-Control"] = "no-store"
    elif path.startswith(("/static/audio/", "/static/covers/")) and status in (200, 206, 304):
        # Privado: ni Cloudflare ni otro proxy compartido guardan archivos que exigen sesión.
        headers["Cache-Control"] = "private, max-age=3600"
        headers["Vary"] = "Cookie"
    elif status in (301, 302, 303, 307, 308, 401, 403):
        headers["Cache-Control"] = "no-store"
    return response


# ---------- compresión de las respuestas JSON de la API ----------
# Las listas largas (p. ej. 1500 canciones, ~1 MB) bajan a ~10 % con gzip. Se omite /api/me
# porque lleva el token CSRF (evita ataques tipo BREACH); el resto no mezcla secretos con texto
# que el visitante controle.
GZIP_MIN_BYTES = 1024
GZIP_SKIP_PATHS = {"/api/me"}


def accepts_gzip():
    for part in request.headers.get("Accept-Encoding", "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() == "gzip":
            return params.replace(" ", "").lower() not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


@app.after_request
def compress_json(response):
    if (
        response.status_code != 200
        or response.direct_passthrough
        or response.mimetype != "application/json"
        or "Content-Encoding" in response.headers
        or not is_api()
        or request.path in GZIP_SKIP_PATHS
    ):
        return response
    data = response.get_data()
    if len(data) < GZIP_MIN_BYTES:
        return response
    response.headers.add("Vary", "Accept-Encoding")
    if not accepts_gzip():
        return response
    response.set_data(gzip.compress(data, compresslevel=5, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    return response


ERROR_TEXT = {
    400: "La petición no es válida.",
    401: "Inicia sesión para continuar.",
    403: "No tienes permiso para esto.",
    404: "No encontré esa página.",
    405: "Método no permitido.",
    413: "El archivo supera 200 MB.",
    429: "Demasiadas peticiones. Espera un momento.",
    500: "Algo falló en el servidor. Intenta de nuevo.",
}


@app.errorhandler(HTTPException)
def http_error(exc):
    code = exc.code or 500
    message = ERROR_TEXT.get(code, "No se pudo completar la acción.")
    if is_api():
        return fail(message, code)
    response = app.make_response((render_template("auth.html", mode="error", code=code, message=message), code))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(Exception)
def server_error(exc):
    if isinstance(exc, HTTPException):
        return http_error(exc)
    log.exception("Error no controlado en %s %s", request.method, request.path)
    if is_api():
        return fail(ERROR_TEXT[500], 500)
    try:
        body = render_template("auth.html", mode="error", code=500, message=ERROR_TEXT[500])
    except Exception:
        body = "Error interno"
    response = app.make_response((body, 500))
    response.headers["Cache-Control"] = "no-store"
    return response


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return fail("Solo el administrador puede hacer esto.", 403)
        return view(*args, **kwargs)

    return wrapper


# ---------- páginas de acceso (sin JavaScript: funcionan en cualquier navegador) ----------

def render_auth(mode, status=200, **context):
    context.setdefault("error", "")
    context.setdefault("notice", "")
    response = app.make_response((
        render_template(
            "auth.html",
            mode=mode,
            csrf=csrf_token(),
            asset_v=ASSET_VERSION,
            min_password=accounts.MIN_PASSWORD,
            registration_open=REGISTRATION_OPEN,
            demo_enabled=demo.DEMO_ENABLED,
            **context,
        ),
        status,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


def form_value(name, limit=200):
    return str(request.form.get(name, ""))[:limit]


@app.route("/login", methods=["GET", "POST"])
def login_page():
    db = get_db()
    if not accounts.admin_exists(db):
        return no_store_redirect("/setup")
    target = safe_next(request.values.get("next"))
    if g.user:
        return no_store_redirect(target)
    if request.method == "GET":
        notice = "Sesión cerrada." if request.args.get("out") else ""
        return render_auth("login", next=target, notice=notice)
    username = form_value("username", 64)
    if not form_csrf_ok():
        return render_auth("login", 400, next=target, username=username, error="El formulario expiró. Intenta de nuevo.")
    try:
        user = accounts.authenticate(db, username, request.form.get("password", ""), client_ip())
    except accounts.AccountError as exc:
        log.info("Ingreso fallido de %s desde %s (%s)", accounts.normalize_username(username)[:32], client_ip(), exc.code)
        return render_auth("login", exc.code, next=target, username=username, error=exc.message)
    login_user(user)
    log.info("Ingreso correcto de %s desde %s", user["username"], client_ip())
    return no_store_redirect("/account/password" if user["must_change_password"] else target)


@app.post("/login/demo")
def demo_login_page():
    """Botón «Probar demo»: entra a la cuenta demo sin escribir la contraseña. Solo con SPOTY_DEMO_ENABLED=1."""
    if not demo.DEMO_ENABLED:
        return no_store_redirect("/login")
    if not form_csrf_ok():
        return render_auth("login", 400, next="/", error="El formulario expiró. Intenta de nuevo.")
    ip = client_ip()
    if accounts.limiter.count(("demo-login", ip), DEMO_LOGIN_WINDOW) >= DEMO_LOGIN_PER_IP:
        return render_auth("login", 429, next="/", error="Demasiados ingresos a la demo desde esta red. Espera unos minutos.")
    accounts.limiter.hit(("demo-login", ip), DEMO_LOGIN_WINDOW)
    user = demo.find_demo(get_db())
    if not user or user["status"] != "active" or user["must_change_password"]:
        return render_auth("login", 503, next="/", error="La cuenta demo no está disponible ahora. Intenta más tarde.")
    login_user(user)
    log.info("Ingreso a la demo desde %s", ip)
    return no_store_redirect("/")


@app.route("/register", methods=["GET", "POST"])
def register_page():
    db = get_db()
    if not accounts.admin_exists(db):
        return no_store_redirect("/setup")
    if g.user:
        return no_store_redirect("/")
    if not REGISTRATION_OPEN:
        return render_auth("closed")
    if request.method == "GET":
        return render_auth("register")
    username = form_value("username", 64)
    display = form_value("display_name", 80)
    keep = {"username": username, "display_name": display}
    if not form_csrf_ok():
        return render_auth("register", 400, error="El formulario expiró. Intenta de nuevo.", **keep)
    ip = client_ip()
    if accounts.limiter.count(("register", ip), accounts.REGISTER_WINDOW) >= accounts.REGISTER_PER_IP:
        return render_auth("register", 429, error="Demasiadas solicitudes desde esta red. Intenta más tarde.", **keep)
    password = request.form.get("password", "")
    if password != request.form.get("confirm", ""):
        return render_auth("register", 400, error="Las contraseñas no coinciden.", **keep)
    try:
        accounts.create_user(db, username, display, password)
    except accounts.AccountError as exc:
        return render_auth("register", exc.code, error=exc.message, **keep)
    accounts.limiter.hit(("register", ip), accounts.REGISTER_WINDOW)
    log.info("Nueva solicitud de cuenta: %s desde %s", accounts.normalize_username(username), ip)
    return render_auth("registered", username=accounts.normalize_username(username))


@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    """Primer arranque: crear la cuenta de administrador. Solo existe mientras no haya uno."""
    db = get_db()
    if accounts.admin_exists(db):
        return no_store_redirect("/login")
    proof = adminpin.proof_kind()
    if proof == "code":
        adminpin.setup_code()
        if request.method == "GET":
            log.warning("Sin administrador: el código de configuración está en %s", adminpin.SETUP_CODE_FILE)
    if request.method == "GET":
        return render_auth("setup", proof=proof)
    username = form_value("username", 64)
    display = form_value("display_name", 80)
    keep = {"username": username, "display_name": display, "proof": proof}
    if not form_csrf_ok():
        return render_auth("setup", 400, error="El formulario expiró. Intenta de nuevo.", **keep)
    ip = client_ip()
    wait = adminpin.locked_for(ip)
    if wait:
        return render_auth("setup", 429, error=f"Demasiados intentos. Espera {max(1, wait // 60)} min.", **keep)
    ok, left = adminpin.check_proof(ip, request.form.get("proof", ""))
    if not ok:
        what = "El PIN" if proof == "pin" else "El código"
        message = f"{what} no es correcto. Te quedan {left} intentos." if left else f"{what} no es correcto. Bloqueado por 10 minutos."
        return render_auth("setup", 401 if left else 429, error=message, **keep)
    password = request.form.get("password", "")
    if password != request.form.get("confirm", ""):
        return render_auth("setup", 400, error="Las contraseñas no coinciden.", **keep)
    try:
        user_id, moved = accounts.create_first_admin(db, username, display, password)
    except accounts.AccountError as exc:
        return render_auth("setup", exc.code, error=exc.message, **keep)
    if proof == "code":
        adminpin.clear_setup_code()
    log.info("Administrador creado (%s). Datos anteriores asignados: %s", accounts.normalize_username(username), moved)
    login_user(accounts.get_user(db, user_id))
    return no_store_redirect("/")


@app.route("/account/password", methods=["GET", "POST"])
def password_page():
    forced = bool(g.user["must_change_password"])
    if is_demo():
        # Visible (para explicar por qué), pero sin formulario; el POST se rechaza igual.
        return render_auth("password", 403 if request.method == "POST" else 200, forced=False, demo=True,
                           error=DEMO_FORBIDDEN if request.method == "POST" else "")
    if request.method == "GET":
        return render_auth("password", forced=forced)
    if not form_csrf_ok():
        return render_auth("password", 400, forced=forced, error="El formulario expiró. Intenta de nuevo.")
    if not accounts.verify_password(get_db(), uid(), request.form.get("current", "")):
        return render_auth("password", 401, forced=forced, error="La contraseña actual no es correcta.")
    password = request.form.get("password", "")
    if password != request.form.get("confirm", ""):
        return render_auth("password", 400, forced=forced, error="Las contraseñas nuevas no coinciden.")
    if password == request.form.get("current", ""):
        return render_auth("password", 400, forced=forced, error="Elige una contraseña distinta a la actual.")
    try:
        accounts.set_password(get_db(), uid(), password, g.user["username"])
    except accounts.AccountError as exc:
        return render_auth("password", exc.code, forced=forced, error=exc.message)
    login_user(accounts.get_user(get_db(), uid()))
    return no_store_redirect("/")


def logout_response(response):
    session.clear()
    # Borra la caché del navegador y del service worker de este sitio (datos del usuario anterior).
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/logout")
def logout_page():
    if g.user and not form_csrf_ok():
        return no_store_redirect("/")
    return logout_response(redirect("/login?out=1"))


@app.post("/api/auth/logout")
def api_logout():
    return logout_response(jsonify(ok=True))


@app.get("/api/me")
def me():
    data = {
        "user": accounts.public_user(g.user),
        "csrf": csrf_token(),
        "admin": is_admin(),
        "demo": is_demo(),
    }
    if is_admin():
        data["pending"] = accounts.pending_count(get_db())
    return jsonify(data)


# ---------- administración de cuentas ----------

@app.get("/api/admin/users")
@require_admin
def admin_users():
    db = get_db()
    return jsonify(users=accounts.list_users(db), pending=accounts.pending_count(db), me=uid())


@app.post("/api/admin/users/<int:user_id>/<action>")
@require_admin
def admin_user_action(user_id, action):
    if action not in ("approve", "reject", "block", "unblock", "reset-password"):
        return fail("Acción no válida.", 404)
    db = get_db()
    try:
        user, extra = accounts.admin_action(db, g.user, user_id, action)
    except accounts.AccountError as exc:
        return fail(exc.message, exc.code)
    log.info("Admin %s: %s -> usuario %s", g.user["username"], action, user_id)
    return jsonify(user=user, pending=accounts.pending_count(db), **extra)


@app.delete("/api/admin/users/<int:user_id>")
@require_admin
def admin_delete_user(user_id):
    db = get_db()
    try:
        covers = accounts.delete_user(db, g.user, user_id)
    except accounts.AccountError as exc:
        return fail(exc.message, exc.code)
    remove_playlist_covers(covers)
    log.info("Admin %s borró al usuario %s", g.user["username"], user_id)
    return jsonify(ok=True, pending=accounts.pending_count(db))


# ---------- catálogo y datos por usuario ----------

def tracks_where(extra_sql="", params=(), order="t.id", limit=None, offset=None):
    sql = TRACK_SQL + " " + extra_sql
    params = (uid(), *params)
    if order:
        sql += f" ORDER BY {order}"
    if offset:
        sql += " LIMIT %s OFFSET %s"
        params = (*params, limit or 10**9, offset)
    elif limit:
        sql += " LIMIT %s"
        params = (*params, limit)
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        tracks = [serialize_track(row) for row in cur.fetchall()]
    devicelibrary.apply_song_likes(get_db(), tracks, uid())
    return tracks


def serialize_track(row):
    data = row_dict(row)
    data["liked"] = bool(data.pop("liked"))
    data["audio_url"] = f"/static/audio/{data['audio_file']}"
    data["cover_url"] = f"/static/covers/{data['cover']}"
    data["song_key"] = song_key(data.get("title"), data.get("artist"), data.get("duration_ms"))
    return data


def playlist_row(row, with_counts=True):
    data = row_dict(row)
    data["editorial"] = bool(data["editorial"])
    data.pop("owner_id", None)
    data["cover_url"] = f"/static/covers/{data['cover_file']}"
    return data


VISIBLE_PLAYLISTS = "(editorial = 1 OR owner_id = %s)"


def greeting():
    now = datetime.now().astimezone()
    hour = now.hour
    if hour < 12:
        hello = "Buenos días"
    elif hour < 19:
        hello = "Buenas tardes"
    else:
        hello = "Buenas noches"
    label = f"{DAYS[now.weekday()]} {now.day} de {MONTHS[now.month - 1]}"
    return hello, label


def like_pattern(value):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/manifest.webmanifest")
def web_manifest():
    path = ROOT_DIR / "pwa" / "manifest.webmanifest"
    return send_file(path, mimetype="application/manifest+json", max_age=0)


@app.get("/sw.js")
def service_worker():
    path = ROOT_DIR / "pwa" / "sw.js"
    body = path.read_text(encoding="utf-8").replace("__ASSET_V__", ASSET_VERSION)
    response = app.response_class(body, mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.route("/")
def index():
    try:
        get_db().ping(reconnect=True)
    except Exception:
        log.exception("No se pudo leer la base de datos")
        return render_template("db_error.html", message="No se pudo conectar con la base de datos. Revisa el servicio MariaDB."), 500
    response = app.make_response(render_template("index.html", asset_v=ASSET_VERSION))
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) AS tracks FROM tracks")
        tracks = cur.fetchone()["tracks"]
    return jsonify(ok=True, tracks=tracks)


@app.get("/api/home")
def home():
    hello, label = greeting()
    with get_db().cursor() as cur:
        cur.execute(
            f"""
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            WHERE {VISIBLE_PLAYLISTS}
            ORDER BY editorial ASC, IF(editorial = 1, id, -id) ASC
            """,
            (uid(),),
        )
        playlists = [playlist_row(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT al.id, al.title, al.year, al.cover_file, al.accent_a, al.accent_b,
                   a.id AS artist_id, a.name AS artist
            FROM albums al JOIN artists a ON a.id = al.artist_id
            ORDER BY al.year DESC, al.id
            """
        )
        albums = []
        for row in cur.fetchall():
            item = row_dict(row)
            item["cover_url"] = f"/static/covers/{item['cover_file']}"
            albums.append(item)
        cur.execute(
            "SELECT id, name, genre, origin, cover_file, accent_a, accent_b FROM artists ORDER BY name"
        )
        artists = []
        for row in cur.fetchall():
            item = row_dict(row)
            item["cover_url"] = f"/static/covers/{item['cover_file']}"
            artists.append(item)
    recent = tracks_where(
        """
        JOIN (
          SELECT track_id, MAX(played_at) AS played_at
          FROM play_history WHERE user_id = %s GROUP BY track_id
        ) h ON h.track_id = t.id
        """,
        (uid(),),
        order="h.played_at DESC",
        limit=8,
    )
    popular = tracks_where("WHERE t.plays > 0", order="t.plays DESC, t.id", limit=8)
    editorial = [item for item in playlists if item["editorial"]]
    featured = playlist_detail(editorial[0]["id"]) if editorial else None
    return jsonify(
        greeting=hello,
        date_label=label,
        featured=featured,
        playlists=playlists,
        albums=albums,
        artists=artists,
        recent=recent,
        popular=popular,
    )


def playlist_detail(playlist_id):
    """Detalle de una lista visible para el usuario (editorial o propia). None si no la puede ver."""
    with get_db().cursor() as cur:
        cur.execute(f"SELECT * FROM playlists WHERE id = %s AND {VISIBLE_PLAYLISTS}", (playlist_id, uid()))
        row = cur.fetchone()
    if not row:
        return None
    data = playlist_row(row)
    catalog = tracks_where(
        "JOIN playlist_tracks pt ON pt.track_id = t.id WHERE pt.playlist_id = %s",
        (playlist_id,),
        order="pt.position, pt.added_at",
    )
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT track_id, position, added_at FROM playlist_tracks WHERE playlist_id = %s",
            (playlist_id,),
        )
        order = {item["track_id"]: (item["position"], item["added_at"]) for item in cur.fetchall()}
    for track in catalog:
        position, added_at = order.get(track["id"], (0, None))
        track["_position"] = position
        track["_added_at"] = added_at
    device = devicelibrary.playlist_tracks(get_db(), playlist_id, uid())
    merged = catalog + device
    merged.sort(key=lambda track: (track.get("_position") or 0, str(track.get("_added_at") or ""), track["title"]))
    for track in merged:
        track.pop("_position", None)
        track.pop("_added_at", None)
    devicelibrary.apply_song_likes(get_db(), merged, uid())
    data["tracks"] = merged
    data["duration_ms"] = sum(track["duration_ms"] for track in merged)
    return data


def own_playlist_or_error(playlist_id):
    """Lista propia del usuario para editar. Devuelve (fila, None) o (None, respuesta de error)."""
    with get_db().cursor() as cur:
        cur.execute("SELECT id, name, description, editorial, owner_id, cover_file FROM playlists WHERE id = %s", (playlist_id,))
        row = cur.fetchone()
    if not row or (not row["editorial"] and row["owner_id"] != uid()):
        return None, fail("Lista no encontrada.", 404)
    if row["editorial"]:
        return None, fail("Las listas editoriales no se editan.", 403)
    return row, None


@app.get("/api/playlists")
def list_playlists():
    with get_db().cursor() as cur:
        cur.execute(
            f"""
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            WHERE {VISIBLE_PLAYLISTS}
            ORDER BY editorial ASC, IF(editorial = 1, id, -id) ASC
            """,
            (uid(),),
        )
        return jsonify([playlist_row(row) for row in cur.fetchall()])


@app.get("/api/playlists/mine")
def my_playlists():
    """Listas propias para el selector «Agregar a playlist»: portada, cantidad y si ya tienen la pista."""
    track_id = request.args.get("track_id", type=int)
    path = str(request.args.get("path", "")).replace("\\", "/").strip("/")[:512]
    rows = playlistedit.own_playlists(get_db(), uid(), track_id=track_id, path=path or None)
    result = []
    for row in rows:
        item = playlist_row(row)
        item["track_count"] = row["track_count"]
        item["contains"] = row["contains"]
        result.append(item)
    return jsonify(result)


@app.post("/api/playlists")
def create_playlist():
    body = request.get_json(silent=True) or {}
    name = " ".join(str(body.get("name", "")).split())
    description = str(body.get("description", "")).strip()
    if not name or len(name) > 80:
        return fail("Escribe un nombre de hasta 80 caracteres.")
    if len(description) > 280:
        return fail("La descripción admite hasta 280 caracteres.")
    written = []
    owner = uid()
    if is_demo():
        with get_db().cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM playlists WHERE editorial = 0 AND owner_id = %s", (owner,))
            if cur.fetchone()["n"] >= demo.DEMO_MAX_PLAYLISTS:
                return fail(f"La cuenta demo admite hasta {demo.DEMO_MAX_PLAYLISTS} listas. Se reinicia cada noche.", 403)

    def create_row(cur):
        cur.execute("SELECT COUNT(*) AS total FROM playlists WHERE editorial = 0 AND owner_id = %s", (owner,))
        index = cur.fetchone()["total"]
        accent_a, accent_b = PALETTE[index % len(PALETTE)]
        cur.execute(
            """
            INSERT INTO playlists (name, description, editorial, accent_a, accent_b, cover_file, owner_id)
            VALUES (%s, %s, 0, %s, %s, %s, %s)
            """,
            (name, description, accent_a, accent_b, "pending.svg", owner),
        )
        new_id = cur.lastrowid
        cover = f"user-{new_id}.svg"
        write_playlist_cover(cover, accent_a, accent_b, name)
        written.append(cover)
        cur.execute("UPDATE playlists SET cover_file = %s WHERE id = %s", (cover, new_id))
        return new_id

    try:
        new_id, result = playlistedit.create_with_items(get_db(), create_row, body, owner, is_admin())
    except playlistedit.PlaylistError as exc:
        remove_playlist_covers(written)
        return fail(exc.message, exc.code)
    except Exception:
        remove_playlist_covers(written)
        raise
    data = playlist_detail(new_id)
    data["result"] = result
    return jsonify(data), 201


def remove_playlist_covers(names):
    for name in names:
        if not name or "/" in name or "\\" in name or not name.startswith("user-"):
            continue
        try:
            (ROOT_DIR / "static" / "covers" / name).unlink(missing_ok=True)
        except OSError:
            pass


@app.patch("/api/playlists/<int:playlist_id>")
def update_playlist(playlist_id):
    current, error = own_playlist_or_error(playlist_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    name = " ".join(str(body.get("name", current["name"])).split())
    description = str(body.get("description", current["description"])).strip()
    if not name or len(name) > 80:
        return fail("Escribe un nombre de hasta 80 caracteres.")
    if len(description) > 280:
        return fail("La descripción admite hasta 280 caracteres.")
    with get_db().cursor() as cur:
        cur.execute(
            "UPDATE playlists SET name = %s, description = %s WHERE id = %s AND owner_id = %s",
            (name, description, playlist_id, uid()),
        )
    return jsonify(playlist_detail(playlist_id))


@app.delete("/api/playlists/<int:playlist_id>")
def delete_playlist(playlist_id):
    current, error = own_playlist_or_error(playlist_id)
    if error:
        return error
    with get_db().cursor() as cur:
        cur.execute("DELETE FROM playlists WHERE id = %s AND owner_id = %s", (playlist_id, uid()))
    cover = str(current.get("cover_file") or "")
    if cover == f"user-{playlist_id}.svg":
        remove_playlist_covers([cover])
    return jsonify(ok=True)


@app.get("/api/playlists/<int:playlist_id>")
def get_playlist(playlist_id):
    data = playlist_detail(playlist_id)
    if not data:
        return fail("Lista no encontrada.", 404)
    return jsonify(data)


@app.post("/api/playlists/<int:playlist_id>/tracks")
def add_playlist_track(playlist_id):
    """Agrega una o varias pistas (track_id, track_ids, keys, paths o source álbum/lista). Omite repetidas."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(playlistedit.add_items(get_db(), playlist_id, body, uid(), is_admin()))
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)


@app.post("/api/playlists/<int:playlist_id>/tracks/remove")
def remove_playlist_tracks(playlist_id):
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(playlistedit.remove_items(get_db(), playlist_id, body.get("keys"), uid()))
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)


@app.delete("/api/playlists/<int:playlist_id>/tracks/<int:track_id>")
def remove_playlist_track(playlist_id, track_id):
    try:
        return jsonify(playlistedit.remove_items(get_db(), playlist_id, [f"t:{track_id}"], uid()))
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)


@app.put("/api/playlists/<int:playlist_id>/order")
def reorder_playlist(playlist_id):
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(playlistedit.reorder(get_db(), playlist_id, body.get("keys"), uid()))
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)


@app.get("/api/albums/<int:album_id>")
def get_album(album_id):
    with get_db().cursor() as cur:
        cur.execute(
            """
            SELECT al.*, a.name AS artist, a.id AS artist_id, a.origin
            FROM albums al JOIN artists a ON a.id = al.artist_id
            WHERE al.id = %s
            """,
            (album_id,),
        )
        row = cur.fetchone()
    if not row:
        return fail("Disco no encontrado.", 404)
    data = row_dict(row)
    data["cover_url"] = f"/static/covers/{data['cover_file']}"
    data["tracks"] = tracks_where("WHERE t.album_id = %s", (album_id,), order="t.track_number")
    data["duration_ms"] = sum(track["duration_ms"] for track in data["tracks"])
    return jsonify(data)


@app.get("/api/artists/<int:artist_id>")
def get_artist(artist_id):
    with get_db().cursor() as cur:
        cur.execute("SELECT * FROM artists WHERE id = %s", (artist_id,))
        row = cur.fetchone()
        if not row:
            return fail("Artista no encontrado.", 404)
        artist = row_dict(row)
        artist["cover_url"] = f"/static/covers/{artist['cover_file']}"
        cur.execute(
            """
            SELECT id, title, year, cover_file, accent_a, accent_b
            FROM albums WHERE artist_id = %s ORDER BY year, id
            """,
            (artist_id,),
        )
        albums = []
        for album in cur.fetchall():
            item = row_dict(album)
            item["cover_url"] = f"/static/covers/{item['cover_file']}"
            albums.append(item)
    artist["albums"] = albums
    artist["top"] = tracks_where("WHERE t.artist_id = %s", (artist_id,), order="t.plays DESC, t.track_number", limit=10)
    return jsonify(artist)


@app.get("/api/likes")
def likes():
    catalog = tracks_where("WHERE l.track_id IS NOT NULL", order="l.liked_at DESC")
    with get_db().cursor() as cur:
        cur.execute("SELECT track_id, liked_at FROM user_likes WHERE user_id = %s", (uid(),))
        stamps = {row["track_id"]: row["liked_at"] for row in cur.fetchall()}
    for track in catalog:
        track["_liked_at"] = stamps.get(track["id"])
    merged = catalog + devicelibrary.liked_tracks(get_db(), uid())
    merged.sort(key=lambda track: str(track.get("_liked_at") or ""), reverse=True)
    unique = []
    seen = set()
    for track in merged:
        key = track.get("song_key") or song_key(track.get("title"), track.get("artist"), track.get("duration_ms"))
        track["song_key"] = key
        if key in seen:
            continue
        seen.add(key)
        track.pop("_liked_at", None)
        unique.append(track)
    return jsonify(unique)


@app.post("/api/likes/<int:track_id>")
def toggle_like(track_id):
    body = request.get_json(silent=True) or {}
    shown = body.get("shown_liked")
    if shown is not None:
        shown = bool(shown)
    try:
        result = devicelibrary.toggle_catalog_like(get_db(), track_id, shown, uid())
    except devicelibrary.LibraryError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.post("/api/plays")
def register_play():
    body = request.get_json(silent=True) or {}
    try:
        track_id = int(body.get("track_id"))
    except (TypeError, ValueError):
        return fail("Falta la pista.")
    with get_db().cursor() as cur:
        cur.execute("SELECT id FROM tracks WHERE id = %s", (track_id,))
        if not cur.fetchone():
            return fail("Pista no encontrada.", 404)
        if not is_demo():
            # Las visitas a la demo no cambian el contador global (lo más escuchado del dueño).
            cur.execute("UPDATE tracks SET plays = plays + 1 WHERE id = %s", (track_id,))
        cur.execute("INSERT INTO play_history (track_id, user_id) VALUES (%s, %s)", (track_id, uid()))
    return jsonify(ok=True)


@app.get("/api/tracks/<int:track_id>/related")
def related(track_id):
    with get_db().cursor() as cur:
        cur.execute("SELECT genre FROM tracks WHERE id = %s", (track_id,))
        row = cur.fetchone()
    if not row:
        return fail("Pista no encontrada.", 404)
    items = tracks_where(
        "WHERE t.genre = %s AND t.id <> %s",
        (row["genre"], track_id),
        order="t.plays DESC, t.id",
        limit=8,
    )
    return jsonify(items)


@app.get("/api/genres")
def genre_list():
    """Géneros de las canciones cargadas (normalizados), con cantidad y duración."""
    index = genres.get_index(get_db())
    items = [genres.public(item) for item in index.values()]
    return jsonify(genres=items, total=sum(1 for item in items if item["slug"] != genres.NO_GENRE_SLUG))


@app.get("/api/genres/<slug>")
def genre_detail(slug):
    if not genres.valid_slug(slug):
        return fail("Género no encontrado.", 404)
    item = genres.get_index(get_db()).get(slug)
    if not item:
        return fail("Género no encontrado.", 404)
    ids = item["track_ids"]
    placeholders = ", ".join(["%s"] * len(ids))
    tracks = tracks_where(
        f"WHERE t.id IN ({placeholders})",
        tuple(ids),
        order="a.name, al.year, al.title, t.track_number, t.id",
    )
    return jsonify(genre=genres.public(item), tracks=tracks)


@app.get("/api/search")
def search():
    q = str(request.args.get("q", "")).strip()[:120]
    if len(q) < 1:
        items = [genres.public(item) for item in genres.get_index(get_db()).values()]
        return jsonify(query="", genres=items, tracks=[], albums=[], artists=[], playlists=[])
    pattern = like_pattern(q)
    track_limit = 300 if request.args.get("all") == "1" else 24
    with get_db().cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            JOIN albums al ON al.id = t.album_id
            WHERE t.title LIKE %s OR a.name LIKE %s OR al.title LIKE %s OR t.genre LIKE %s
            """,
            (pattern, pattern, pattern, pattern),
        )
        track_total = int(cur.fetchone()["total"])
    found_tracks = tracks_where(
        "WHERE t.title LIKE %s OR a.name LIKE %s OR al.title LIKE %s OR t.genre LIKE %s",
        (pattern, pattern, pattern, pattern),
        order="t.title",
        limit=track_limit,
    )
    with get_db().cursor() as cur:
        cur.execute(
            """
            SELECT al.id, al.title, al.year, al.cover_file, a.name AS artist, a.id AS artist_id
            FROM albums al JOIN artists a ON a.id = al.artist_id
            WHERE al.title LIKE %s OR a.name LIKE %s
            ORDER BY al.title LIMIT 12
            """,
            (pattern, pattern),
        )
        albums = []
        for row in cur.fetchall():
            item = row_dict(row)
            item["cover_url"] = f"/static/covers/{item['cover_file']}"
            albums.append(item)
        cur.execute(
            """
            SELECT id, name, genre, origin, cover_file
            FROM artists
            WHERE name LIKE %s OR genre LIKE %s OR origin LIKE %s
            ORDER BY name LIMIT 12
            """,
            (pattern, pattern, pattern),
        )
        artists = []
        for row in cur.fetchall():
            item = row_dict(row)
            item["cover_url"] = f"/static/covers/{item['cover_file']}"
            artists.append(item)
        cur.execute(
            f"""
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            WHERE (name LIKE %s OR description LIKE %s) AND {VISIBLE_PLAYLISTS}
            ORDER BY name LIMIT 12
            """,
            (pattern, pattern, uid()),
        )
        playlists = [playlist_row(row) for row in cur.fetchall()]
    return jsonify(
        query=q,
        genres=[],
        tracks=found_tracks,
        track_total=track_total,
        albums=albums,
        artists=artists,
        playlists=playlists,
    )


# ---------- carpeta Música del servidor: solo administradores ----------

@app.get("/api/equipo")
@require_admin
def equipo_folder():
    try:
        data = localfiles.list_directory(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    data["liked"] = devicelibrary.mark_liked(get_db(), data["tracks"], uid())
    return jsonify(data)


@app.post("/api/equipo/likes")
@require_admin
def equipo_like():
    body = request.get_json(silent=True) or {}
    shown = body.get("shown_liked")
    if shown is not None:
        shown = bool(shown)
    try:
        result = devicelibrary.toggle_saved(get_db(), str(body.get("path", "")), shown, uid())
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    except devicelibrary.LibraryError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.post("/api/equipo/playlists")
@require_admin
def equipo_add_playlist():
    body = request.get_json(silent=True) or {}
    try:
        playlist_id = int(body.get("playlist_id"))
    except (TypeError, ValueError):
        return fail("Elige una lista.")
    try:
        result = playlistedit.add_items(get_db(), playlist_id, {"paths": [str(body.get("path", ""))]}, uid(), True)
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.delete("/api/playlists/<int:playlist_id>/device")
def equipo_remove_playlist(playlist_id):
    body = request.get_json(silent=True) or {}
    path = str(body.get("path", "")).replace("\\", "/").strip("/")
    try:
        result = playlistedit.remove_items(get_db(), playlist_id, [f"d:{path}"], uid())
    except playlistedit.PlaylistError as exc:
        return fail(exc.message, exc.code)
    result["ok"] = True
    return jsonify(result)


@app.get("/api/equipo/audio")
@require_admin
def equipo_audio():
    try:
        path, mime = localfiles.open_audio(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    return send_file(path, mimetype=mime, conditional=True, max_age=0)


@app.get("/api/equipo/image")
@require_admin
def equipo_image():
    try:
        path, mime = localfiles.open_image(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    return send_file(path, mimetype=mime, conditional=True, max_age=3600)


# ---------- subidas: ver es para todos; subir y eliminar, solo administradores ----------

# Campos que necesita el reproductor y las listas; «lite» omite el resto (slug, portada interna, colores...).
LITE_TRACK_FIELDS = (
    "id", "title", "artist", "artist_id", "album", "album_id", "duration_ms", "audio_url",
    "cover_url", "liked", "song_key", "source", "genre", "bpm", "musical_key",
)
UPLOADS_MAX_PAGE = 5000


def int_arg(name, default, low, high):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(name) from None
    return max(low, min(high, value))


@app.get("/api/uploads")
def list_uploads():
    """Canciones subidas. ?lite=1 entrega solo los campos necesarios; ?limit= y ?offset= paginan."""
    try:
        limit = int_arg("limit", None, 1, UPLOADS_MAX_PAGE)
        offset = int_arg("offset", 0, 0, 10**9)
    except ValueError as exc:
        return fail(f"Parámetro no válido: {exc}.", 400)
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE source = 'user'")
        total = int(cur.fetchone()["n"])
    tracks = tracks_where("WHERE t.source = 'user'", order="t.uploaded_at DESC, t.id DESC", limit=limit, offset=offset)
    if request.args.get("lite") == "1":
        tracks = [{key: track.get(key) for key in LITE_TRACK_FIELDS} for track in tracks]
    return jsonify(
        admin=is_admin(),
        max_mb=uploads.MAX_FILE_BYTES // (1024 * 1024),
        total=total,
        offset=offset,
        tracks=tracks,
    )


@app.post("/api/uploads")
@require_admin
def upload_track():
    storage = request.files.get("file")
    if storage is None or not storage.filename:
        return fail("Falta el archivo.")
    try:
        track_id = uploads.store_upload(get_db(), storage)
    except uploads.UploadError as exc:
        return fail(exc.message, exc.code, **exc.extra)
    found = tracks_where("WHERE t.id = %s", (track_id,))
    return jsonify(found[0] if found else {"id": track_id}), 201


@app.delete("/api/tracks/<int:track_id>")
@require_admin
def delete_track(track_id):
    try:
        uploads.delete_upload(get_db(), track_id)
    except uploads.UploadError as exc:
        return fail(exc.message, exc.code)
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"SpotyOFO en http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
