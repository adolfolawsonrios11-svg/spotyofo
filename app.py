"""SpotyOFO: reproductor local sin cuentas."""

import html
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, render_template, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix

import adminpin
import devicelibrary
import localfiles
import uploads
from config import HOST, PORT, TRUST_PROXY
from db import TRACK_SQL, connect, init_schema, row_dict
from songs import song_key

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
# 200 MB por archivo + margen para el sobre multipart. Se sube un archivo por petición.
app.config["MAX_CONTENT_LENGTH"] = uploads.MAX_FILE_BYTES + 2 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["SESSION_COOKIE_NAME"] = "spotyofo_session"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.secret_key = adminpin.load_secret_key()
if TRUST_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PREFERRED_URL_SCHEME"] = "https"
uploads.ensure_dirs()
uploads.purge_pending()

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
PALETTE = [
    ("#12343c", "#7dffe0"),
    ("#241018", "#ff6d93"),
    ("#1a1430", "#b7a6ff"),
    ("#3a2410", "#ffc56d"),
    ("#241612", "#ff7358"),
    ("#10243a", "#8fd4ff"),
]


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


@app.errorhandler(413)
def too_large(_error):
    return fail("El archivo supera 200 MB.", 413)


def same_origin():
    """Bloquea peticiones de otros sitios: exige la cabecera propia y, si viene, un Origin igual al host."""
    if request.headers.get("X-Requested-With") != "SpotyOFO":
        return False
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return False
    return True


def is_admin():
    record = adminpin.read_pin()
    return bool(record) and session.get("admin") == record["nonce"]


def require_pin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not same_origin():
            return fail("Petición rechazada.", 403)
        if not adminpin.is_configured():
            return fail("Primero crea un PIN.", 401, needs="setup")
        if not is_admin():
            return fail("Ingresa tu PIN para continuar.", 401, needs="login")
        return view(*args, **kwargs)

    return wrapper


def login_session(record):
    session.clear()
    session.permanent = True
    session["admin"] = record["nonce"]


def tracks_where(extra_sql="", params=(), order="t.id", limit=None):
    sql = TRACK_SQL + " " + extra_sql
    if order:
        sql += f" ORDER BY {order}"
    if limit:
        sql += " LIMIT %s"
        params = (*params, limit)
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        tracks = [serialize_track(row) for row in cur.fetchall()]
    devicelibrary.apply_song_likes(get_db(), tracks)
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
    data["cover_url"] = f"/static/covers/{data['cover_file']}"
    return data


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


def write_playlist_cover(filename, c1, c2, label):
    initial = html.escape((label[:1] or "S").upper())
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="640" height="640" fill="url(#bg)"/>
  <circle cx="460" cy="180" r="90" fill="#ffffff" fill-opacity="0.18"/>
  <circle cx="180" cy="460" r="120" fill="#000000" fill-opacity="0.18"/>
  <text x="72" y="430" fill="#ffffff" font-family="Segoe UI, sans-serif" font-size="280" font-weight="700">{initial}</text>
</svg>
"""
    path = Path(__file__).resolve().parent / "static" / "covers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def like_pattern(value):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/manifest.webmanifest")
def web_manifest():
    path = Path(__file__).resolve().parent / "pwa" / "manifest.webmanifest"
    return send_file(path, mimetype="application/manifest+json", max_age=0)


@app.get("/sw.js")
def service_worker():
    path = Path(__file__).resolve().parent / "pwa" / "sw.js"
    response = send_file(path, mimetype="application/javascript", max_age=0)
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/")
def index():
    try:
        init_schema(get_db())
        with get_db().cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM tracks")
            total = cur.fetchone()["total"]
    except Exception as exc:
        return render_template("db_error.html", message=str(exc)), 500
    if not total:
        return render_template("db_error.html", message="La base está vacía. Ejecuta python synthesize.py"), 500
    return render_template("index.html")


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
            """
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            ORDER BY editorial ASC, IF(editorial = 1, id, -id) ASC
            """
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
          FROM play_history GROUP BY track_id
        ) h ON h.track_id = t.id
        """,
        order="h.played_at DESC",
        limit=8,
    )
    popular = tracks_where("WHERE t.plays > 0", order="t.plays DESC, t.id", limit=8)
    featured = None
    if playlists:
        editorial = [item for item in playlists if item["editorial"]]
        featured = playlist_detail((editorial or playlists)[0]["id"])
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
    with get_db().cursor() as cur:
        cur.execute("SELECT * FROM playlists WHERE id = %s", (playlist_id,))
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
    device = devicelibrary.playlist_tracks(get_db(), playlist_id)
    merged = catalog + device
    merged.sort(key=lambda track: (track.get("_position") or 0, str(track.get("_added_at") or ""), track["title"]))
    for track in merged:
        track.pop("_position", None)
        track.pop("_added_at", None)
    devicelibrary.apply_song_likes(get_db(), merged)
    data["tracks"] = merged
    data["duration_ms"] = sum(track["duration_ms"] for track in merged)
    return data


@app.get("/api/playlists")
def list_playlists():
    with get_db().cursor() as cur:
        cur.execute(
            """
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            ORDER BY editorial ASC, IF(editorial = 1, id, -id) ASC
            """
        )
        return jsonify([playlist_row(row) for row in cur.fetchall()])


@app.post("/api/playlists")
def create_playlist():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    description = str(body.get("description", "")).strip()
    if not name or len(name) > 80:
        return fail("Escribe un nombre de hasta 80 caracteres.")
    if len(description) > 280:
        return fail("La descripción admite hasta 280 caracteres.")
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM playlists WHERE editorial = 0")
        index = cur.fetchone()["total"]
        accent_a, accent_b = PALETTE[index % len(PALETTE)]
        cur.execute(
            """
            INSERT INTO playlists (name, description, editorial, accent_a, accent_b, cover_file)
            VALUES (%s, %s, 0, %s, %s, %s)
            """,
            (name, description, accent_a, accent_b, "pending.svg"),
        )
        new_id = cur.lastrowid
        cover = f"user-{new_id}.svg"
        write_playlist_cover(cover, accent_a, accent_b, name)
        cur.execute("UPDATE playlists SET cover_file = %s WHERE id = %s", (cover, new_id))
    return jsonify(playlist_detail(new_id)), 201


@app.patch("/api/playlists/<int:playlist_id>")
def update_playlist(playlist_id):
    current = playlist_detail(playlist_id)
    if not current:
        return fail("Lista no encontrada.", 404)
    if current["editorial"]:
        return fail("Las listas editoriales no se editan.", 403)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", current["name"])).strip()
    description = str(body.get("description", current["description"])).strip()
    if not name or len(name) > 80:
        return fail("Escribe un nombre de hasta 80 caracteres.")
    if len(description) > 280:
        return fail("La descripción admite hasta 280 caracteres.")
    with get_db().cursor() as cur:
        cur.execute(
            "UPDATE playlists SET name = %s, description = %s WHERE id = %s",
            (name, description, playlist_id),
        )
    return jsonify(playlist_detail(playlist_id))


@app.delete("/api/playlists/<int:playlist_id>")
def delete_playlist(playlist_id):
    current = playlist_detail(playlist_id)
    if not current:
        return fail("Lista no encontrada.", 404)
    if current["editorial"]:
        return fail("Las listas editoriales no se eliminan.", 403)
    with get_db().cursor() as cur:
        cur.execute("DELETE FROM playlists WHERE id = %s", (playlist_id,))
    return jsonify(ok=True)


@app.get("/api/playlists/<int:playlist_id>")
def get_playlist(playlist_id):
    data = playlist_detail(playlist_id)
    if not data:
        return fail("Lista no encontrada.", 404)
    return jsonify(data)


@app.post("/api/playlists/<int:playlist_id>/tracks")
def add_playlist_track(playlist_id):
    current = playlist_detail(playlist_id)
    if not current:
        return fail("Lista no encontrada.", 404)
    if current["editorial"]:
        return fail("Las listas editoriales están cerradas. Crea una lista tuya.", 403)
    body = request.get_json(silent=True) or {}
    try:
        track_id = int(body.get("track_id"))
    except (TypeError, ValueError):
        return fail("Falta la pista.")
    with get_db().cursor() as cur:
        cur.execute("SELECT id FROM tracks WHERE id = %s", (track_id,))
        if not cur.fetchone():
            return fail("Pista no encontrada.", 404)
        cur.execute(
            "SELECT COALESCE(MAX(position), -1) AS max_pos FROM playlist_tracks WHERE playlist_id = %s",
            (playlist_id,),
        )
        position = cur.fetchone()["max_pos"] + 1
        cur.execute(
            """
            INSERT INTO playlist_tracks (playlist_id, track_id, position)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE position = position
            """,
            (playlist_id, track_id, position),
        )
    return jsonify(playlist_detail(playlist_id))


@app.delete("/api/playlists/<int:playlist_id>/tracks/<int:track_id>")
def remove_playlist_track(playlist_id, track_id):
    current = playlist_detail(playlist_id)
    if not current:
        return fail("Lista no encontrada.", 404)
    if current["editorial"]:
        return fail("Las listas editoriales están cerradas.", 403)
    with get_db().cursor() as cur:
        cur.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = %s AND track_id = %s",
            (playlist_id, track_id),
        )
    return jsonify(playlist_detail(playlist_id))


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
        cur.execute("SELECT track_id, liked_at FROM liked_tracks")
        stamps = {row["track_id"]: row["liked_at"] for row in cur.fetchall()}
    for track in catalog:
        track["_liked_at"] = stamps.get(track["id"])
    merged = catalog + devicelibrary.liked_tracks(get_db())
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
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = request.get_json(silent=True) or {}
    shown = body.get("shown_liked")
    if shown is not None:
        shown = bool(shown)
    try:
        result = devicelibrary.toggle_catalog_like(get_db(), track_id, shown)
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
        cur.execute("UPDATE tracks SET plays = plays + 1 WHERE id = %s", (track_id,))
        cur.execute("INSERT INTO play_history (track_id) VALUES (%s)", (track_id,))
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


@app.get("/api/search")
def search():
    q = str(request.args.get("q", "")).strip()
    if len(q) < 1:
        with get_db().cursor() as cur:
            cur.execute("SELECT DISTINCT genre FROM tracks ORDER BY genre")
            genres = [row["genre"] for row in cur.fetchall()]
        return jsonify(query="", genres=genres, tracks=[], albums=[], artists=[], playlists=[])
    pattern = like_pattern(q)
    found_tracks = tracks_where(
        "WHERE t.title LIKE %s OR a.name LIKE %s OR al.title LIKE %s OR t.genre LIKE %s",
        (pattern, pattern, pattern, pattern),
        order="t.title",
        limit=24,
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
            """
            SELECT id, name, description, editorial, accent_a, accent_b, cover_file, created_at
            FROM playlists
            WHERE name LIKE %s OR description LIKE %s
            ORDER BY name LIMIT 12
            """,
            (pattern, pattern),
        )
        playlists = [playlist_row(row) for row in cur.fetchall()]
    return jsonify(query=q, genres=[], tracks=found_tracks, albums=albums, artists=artists, playlists=playlists)


def auth_state():
    return {"configured": adminpin.is_configured(), "authenticated": is_admin()}


def pin_body():
    body = request.get_json(silent=True) or {}
    return {key: str(body.get(key, "")).strip() for key in ("pin", "confirm", "current")}


@app.get("/api/auth")
def auth_status():
    return jsonify(auth_state())


@app.post("/api/auth/setup")
def auth_setup():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = pin_body()
    if not adminpin.valid_pin(body["pin"]):
        return fail("El PIN debe tener entre 4 y 12 dígitos.")
    if body["pin"] != body["confirm"]:
        return fail("Los PIN no coinciden.")
    record = adminpin.create_pin_once(body["pin"])
    if record is None:
        return fail("Ya existe un PIN. Ingrésalo o cámbialo con el actual.", 409)
    login_session(record)
    return jsonify(auth_state())


def pin_attempt(pin):
    ip = request.remote_addr or "?"
    wait = adminpin.locked_for(ip)
    if wait:
        return None, fail(f"Demasiados intentos. Espera {max(1, wait // 60)} min.", 429)
    ok, record, left = adminpin.check_pin(ip, pin)
    if record is None:
        return None, fail("Primero crea un PIN.", 401, needs="setup")
    if not ok:
        if left <= 0:
            return None, fail("PIN incorrecto. Bloqueado por 10 minutos.", 429)
        return None, fail(f"PIN incorrecto. Te quedan {left} intentos.", 401, needs="login")
    return record, None


@app.post("/api/auth/login")
def auth_login():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    record, error = pin_attempt(pin_body()["pin"])
    if error:
        return error
    login_session(record)
    return jsonify(auth_state())


@app.post("/api/auth/change")
def auth_change():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = pin_body()
    if not adminpin.valid_pin(body["pin"]):
        return fail("El PIN nuevo debe tener entre 4 y 12 dígitos.")
    if body["pin"] != body["confirm"]:
        return fail("Los PIN nuevos no coinciden.")
    record, error = pin_attempt(body["current"])
    if error:
        return error
    login_session(adminpin.save_pin(body["pin"]))
    return jsonify(auth_state())


@app.post("/api/auth/logout")
def auth_logout():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    session.clear()
    return jsonify(auth_state())


@app.get("/api/equipo")
def equipo_folder():
    try:
        data = localfiles.list_directory(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    data["liked"] = devicelibrary.mark_liked(get_db(), data["tracks"])
    return jsonify(data)


@app.post("/api/equipo/likes")
def equipo_like():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = request.get_json(silent=True) or {}
    shown = body.get("shown_liked")
    if shown is not None:
        shown = bool(shown)
    try:
        result = devicelibrary.toggle_saved(get_db(), str(body.get("path", "")), shown)
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    except devicelibrary.LibraryError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.post("/api/equipo/playlists")
def equipo_add_playlist():
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = request.get_json(silent=True) or {}
    try:
        playlist_id = int(body.get("playlist_id"))
    except (TypeError, ValueError):
        return fail("Elige una lista.")
    try:
        result = devicelibrary.add_saved_to_playlist(get_db(), str(body.get("path", "")), playlist_id)
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    except devicelibrary.LibraryError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.delete("/api/playlists/<int:playlist_id>/device")
def equipo_remove_playlist(playlist_id):
    if not same_origin():
        return fail("Petición rechazada.", 403)
    body = request.get_json(silent=True) or {}
    try:
        result = devicelibrary.remove_saved_from_playlist(get_db(), playlist_id, str(body.get("path", "")).replace("\\", "/").strip("/"))
    except devicelibrary.LibraryError as exc:
        return fail(exc.message, exc.code)
    return jsonify(result)


@app.get("/api/equipo/audio")
def equipo_audio():
    try:
        path, mime = localfiles.open_audio(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    return send_file(path, mimetype=mime, conditional=True, max_age=0)


@app.get("/api/equipo/image")
def equipo_image():
    try:
        path, mime = localfiles.open_image(request.args.get("path", ""))
    except localfiles.LocalFileError as exc:
        return fail(exc.message, exc.code)
    return send_file(path, mimetype=mime, conditional=True, max_age=3600)


@app.get("/api/uploads")
def list_uploads():
    tracks = tracks_where("WHERE t.source = 'user'", order="t.uploaded_at DESC, t.id DESC")
    return jsonify(
        auth=auth_state(),
        max_mb=uploads.MAX_FILE_BYTES // (1024 * 1024),
        tracks=tracks,
    )


@app.post("/api/uploads")
@require_pin
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
@require_pin
def delete_track(track_id):
    try:
        uploads.delete_upload(get_db(), track_id)
    except uploads.UploadError as exc:
        return fail(exc.message, exc.code)
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"SpotyOFO en http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
