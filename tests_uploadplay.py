"""Pruebas de «Reproducir todas» en Subir: /api/uploads y permisos (solo biblioteca estándar).

Uso (con la app corriendo, un administrador de prueba y la cuenta demo creada):
  set SPOTY_DEMO_PASSWORD=...        (o en .env)
  python tests_uploadplay.py http://127.0.0.1:5050 --admin zzadmin --admin-pass CLAVE

Crea una cuenta «zzplay» (y la borra al final). No cambia datos de otras cuentas.
La cuenta demo solo lee; quien reinicia sus datos es «python manage.py demo-reset».
"""

import argparse
import gzip
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (carga .env)

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--admin", default="zzadmin")
ap.add_argument("--admin-pass", default="Prueba-Admin-2026!")
ap.add_argument("--demo-user", default=os.environ.get("SPOTY_DEMO_USER", "demo"))
args = ap.parse_args()
BASE = args.base.rstrip("/")
DEMO_PASS = os.environ.get("SPOTY_DEMO_PASSWORD", "")
USER_PASS = "Clave-de-Play-2026"
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:300]}"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)
        self.csrf = ""

    def raw(self, method, path, data=None, headers=None):
        req = urllib.request.Request(BASE + path, method=method, data=data, headers=headers or {})
        try:
            with self.opener.open(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def form(self, path, fields):
        st, h, body = self.raw("GET", path)
        m = re.search(r'name="csrf_token" value="([^"]+)"', body.decode("utf-8", "replace"))
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}
        data = urllib.parse.urlencode({"csrf_token": m.group(1) if m else "", **fields}).encode()
        st, h, body = self.raw("POST", path, data, headers)
        return st, h, body.decode("utf-8", "replace")

    def api(self, method, path, body=None, headers=None, data=None):
        hdrs = {"X-Requested-With": "SpotyOFO", "Accept": "application/json", "X-CSRF-Token": self.csrf}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        hdrs.update(headers or {})
        st, h, raw = self.raw(method, path, data, hdrs)
        try:
            return st, json.loads(raw or b"{}")
        except ValueError:
            return st, {"_raw": raw[:200]}

    def login(self, user, password):
        st, h, text = self.form("/login", {"username": user, "password": password})
        if st in (302, 303):
            code, me = self.api("GET", "/api/me")
            if code == 200:
                self.csrf = me["csrf"]
        return st, text


def ids(data):
    return [t["id"] for t in data.get("tracks", [])]


def total_plays(admin):
    st, data = admin.api("GET", "/api/uploads")
    return sum(int(t.get("plays") or 0) for t in data.get("tracks", []))


# ---------- sin sesión ----------
anon = Client()
st, data = anon.api("GET", "/api/uploads")
check("sin sesión: GET /api/uploads -> 401", st == 401, st)

# ---------- administrador ----------
admin = Client()
st, text = admin.login(args.admin, args.admin_pass)
check("login del administrador de prueba", st in (302, 303), (st, text[:100]))
st, up = admin.api("GET", "/api/uploads")
check("admin: GET /api/uploads 200 y admin=true", st == 200 and up.get("admin") is True, (st, up.get("admin")))
tracks = up.get("tracks", [])
check("admin: hay canciones subidas", len(tracks) >= 2, len(tracks))
check("admin: sin ids repetidos", len(set(ids(up))) == len(tracks), len(tracks))
need = {"id", "title", "artist", "album", "album_id", "duration_ms", "audio_url", "cover_url", "liked"}
check("admin: cada pista trae lo que el reproductor necesita", all(need <= set(t) for t in tracks), [need - set(t) for t in tracks][:2])
check("admin: todas son de la fuente «user» (subidas)", all(t.get("source") == "user" for t in tracks), {t.get("source") for t in tracks})
check("admin: orden estable (más nuevas primero)", ids(up) == ids(admin.api("GET", "/api/uploads")[1]), "")
st, health = admin.api("GET", "/api/health")
check("el conteo de subidas coincide con las pistas de la biblioteca", st == 200 and len(tracks) == health.get("tracks"), (len(tracks), health))
# Un audio de la lista se puede pedir (lo que hará el reproductor).
first = tracks[0]
st, h, raw = admin.raw("GET", first["audio_url"], headers={"Range": "bytes=0-99"})
check("el audio de la primera pista responde (206/200)", st in (200, 206) and len(raw) > 0, st)

# ---------- pedir la lista no suma reproducciones ----------
before = total_plays(admin)
for _ in range(3):
    admin.api("GET", "/api/uploads")
check("pedir la lista no suma reproducciones", total_plays(admin) == before, (before, total_plays(admin)))

# ---------- cuenta normal ----------
st, data = admin.api("GET", "/api/admin/users")
for u in data.get("users", []):
    if u["username"] == "zzplay":
        admin.api("DELETE", f"/api/admin/users/{u['id']}")
u = Client()
st, h, text = u.form("/register", {"username": "zzplay", "display_name": "Play Prueba", "password": USER_PASS, "confirm": USER_PASS})
check("registro de la cuenta de prueba", st == 200, st)
st, data = admin.api("GET", "/api/admin/users")
uid = next((x["id"] for x in data["users"] if x["username"] == "zzplay"), None)
st, data = admin.api("POST", f"/api/admin/users/{uid}/approve")
check("aprobar la cuenta de prueba", st == 200, (st, data))
st, text = u.login("zzplay", USER_PASS)
check("login de la cuenta normal", st in (302, 303), st)
st, upu = u.api("GET", "/api/uploads")
check("normal: GET /api/uploads 200 y admin=false", st == 200 and upu.get("admin") is False, (st, upu.get("admin")))
check("normal: ve las mismas canciones que el admin", set(ids(upu)) == set(ids(up)), (len(ids(upu)), len(ids(up))))
check("normal: sin ids repetidos", len(set(ids(upu))) == len(upu.get("tracks", [])), "")
st, _ = u.api("POST", "/api/uploads", headers={"Content-Type": "multipart/form-data; boundary=x"}, data=b"--x--")
check("normal: puede intentar subir (no 403)", st == 400, st)
st, _ = u.api("DELETE", f"/api/tracks/{first['id']}")
check("normal: eliminar -> 403", st == 403, st)
u.api("POST", f"/api/likes/{first['id']}", {"shown_liked": False})
st, upu2 = u.api("GET", "/api/uploads")
check("normal: sus Me gusta se reflejan en la lista", next(t for t in upu2["tracks"] if t["id"] == first["id"])["liked"] is True, "")
st, upa = admin.api("GET", "/api/uploads")
check("los Me gusta de una cuenta no se ven en otra", next(t for t in upa["tracks"] if t["id"] == first["id"])["liked"] == first["liked"], "")
u.api("POST", f"/api/likes/{first['id']}", {"shown_liked": True})

# ---------- cuenta demo ----------
if DEMO_PASS:
    d = Client()
    st, text = d.login(args.demo_user, DEMO_PASS)
    check("login de la demo", st in (302, 303), st)
    st, upd = d.api("GET", "/api/uploads")
    check("demo: GET /api/uploads 200 y admin=false", st == 200 and upd.get("admin") is False, (st, upd.get("admin")))
    check("demo: ve las mismas canciones", set(ids(upd)) == set(ids(up)), (len(ids(upd)), len(ids(up))))
    st, me = d.api("GET", "/api/me")
    check("demo: /api/me demo=true, admin=false", me.get("demo") is True and me.get("admin") is False, me)
    st, _ = d.api("POST", "/api/uploads", headers={"Content-Type": "multipart/form-data; boundary=x"}, data=b"--x--")
    check("demo: subir -> 403", st == 403, st)
    st, _ = d.api("DELETE", f"/api/tracks/{first['id']}")
    check("demo: eliminar -> 403", st == 403, st)
    before = total_plays(admin)
    d.api("POST", "/api/plays", {"track_id": first["id"]})
    check("demo: reproducir no suma al contador global", total_plays(admin) == before, (before, total_plays(admin)))
else:
    print("(sin SPOTY_DEMO_PASSWORD: se omiten las pruebas de la demo)")

# ---------- lista rápida: compresión, modo ligero y paginación ----------
lite_need = {"id", "title", "artist", "artist_id", "album", "album_id", "duration_ms", "audio_url", "cover_url", "liked", "song_key", "source"}
st, h, raw_plain = admin.raw("GET", "/api/uploads", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO"})
check("sin Accept-Encoding: respuesta sin comprimir", st == 200 and "gzip" not in h.get("Content-Encoding", "").lower() and json.loads(raw_plain)["tracks"], (st, h.get("Content-Encoding")))
st, h, raw_gz = admin.raw("GET", "/api/uploads", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO", "Accept-Encoding": "gzip"})
check("con Accept-Encoding: gzip -> Content-Encoding gzip y Vary", st == 200 and h.get("Content-Encoding") == "gzip" and "accept-encoding" in h.get("Vary", "").lower(), (st, h.get("Content-Encoding"), h.get("Vary")))
try:
    unzipped = gzip.decompress(raw_gz)
except Exception as e:  # noqa: BLE001
    unzipped = b""
check("gzip se descomprime y es el mismo JSON", json.loads(unzipped or b"{}") == json.loads(raw_plain), "")
check("gzip reduce el tamaño (si hay muchas pistas)", len(tracks) < 100 or len(raw_gz) < len(raw_plain) * 0.4, (len(raw_gz), len(raw_plain)))
check("gzip: Content-Length coincide con el cuerpo", int(h.get("Content-Length", -1)) == len(raw_gz), (h.get("Content-Length"), len(raw_gz)))
check("gzip: Cache-Control no-store se conserva", "no-store" in h.get("Cache-Control", ""), h.get("Cache-Control"))
st, h, raw_me = admin.raw("GET", "/api/me", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO", "Accept-Encoding": "gzip"})
check("/api/me nunca se comprime (token CSRF)", st == 200 and "Content-Encoding" not in h, (st, h.get("Content-Encoding")))
st, h, raw_q0 = admin.raw("GET", "/api/uploads", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO", "Accept-Encoding": "gzip;q=0"})
check("gzip;q=0 se respeta (sin comprimir)", st == 200 and "Content-Encoding" not in h, (st, h.get("Content-Encoding")))
st, h, _ = anon.raw("GET", "/api/uploads", headers={"Accept-Encoding": "gzip", "X-Requested-With": "SpotyOFO"})
check("sin sesión con gzip: sigue 401", st == 401, st)
st, lite = admin.api("GET", "/api/uploads?lite=1")
lt = lite.get("tracks", [])
check("lite: mismas pistas y mismo orden que la lista completa", st == 200 and ids(lite) == ids(up), (st, len(lt), len(tracks)))
check("lite: trae los campos que necesita el reproductor", all(lite_need <= set(t) for t in lt), [lite_need - set(t) for t in lt][:2])
check("lite: es más ligero que la lista completa", not lt or max(len(t) for t in lt) < min(len(t) for t in tracks), (len(lt[0]) if lt else 0, len(tracks[0])))
check("lite: los valores coinciden con la lista completa", all(all(t.get(k) == f.get(k) for k in lite_need) for t, f in zip(lt, tracks)), "")
check("lite: conserva admin/max_mb y agrega total/offset", lite.get("admin") is True and "max_mb" in lite and lite.get("total") == len(tracks) and lite.get("offset") == 0, {k: lite.get(k) for k in ("admin", "max_mb", "total", "offset")})
check("lista completa sin parámetros: total = cantidad", up.get("total", len(tracks)) == len(tracks), up.get("total"))
page_size = max(1, min(7, len(tracks) // 3 or 1))
got, off, guard = [], 0, 0
while guard < 5000:
    guard += 1
    st, pg = admin.api("GET", f"/api/uploads?lite=1&offset={off}&limit={page_size}")
    if st != 200 or not pg.get("tracks"):
        break
    got += ids(pg)
    off += len(pg["tracks"])
    if off >= pg.get("total", 0):
        break
check("paginación: las páginas suman la lista completa, sin repetidas y en orden", got == ids(up), (len(got), len(ids(up))))
st, pg = admin.api("GET", f"/api/uploads?lite=1&offset={len(tracks)}&limit=5")
check("paginación: offset pasado el final -> lista vacía con total", st == 200 and pg.get("tracks") == [] and pg.get("total") == len(tracks), (st, pg.get("total")))
st, pg = admin.api("GET", "/api/uploads?lite=1&limit=1")
check("paginación: limit=1 devuelve una sola pista", st == 200 and len(pg.get("tracks", [])) == 1 and ids(pg) == ids(up)[:1], st)
for bad in ("limit=abc", "offset=x", "limit=1.5"):
    st, pg = admin.api("GET", f"/api/uploads?lite=1&{bad}")
    check(f"parámetro no numérico ({bad}) -> 400", st == 400, st)
# Los números fuera de rango se ajustan (misma convención que el resto de la API).
st, pg = admin.api("GET", "/api/uploads?lite=1&limit=0")
check("limit=0 se ajusta a 1", st == 200 and len(pg.get("tracks", [])) == 1, (st, len(pg.get("tracks", []))))
st, pg = admin.api("GET", "/api/uploads?lite=1&offset=-5&limit=2")
check("offset negativo se ajusta a 0", st == 200 and ids(pg) == ids(up)[:2] and pg.get("offset") == 0, (st, pg.get("offset")))
st, pg = admin.api("GET", "/api/uploads?lite=1&limit=99999")
check("limit enorme se acota (nunca más de 5000)", st == 200 and len(pg.get("tracks", [])) == min(len(tracks), 5000), (st, len(pg.get("tracks", []))))
st, pgu = u.api("GET", "/api/uploads?lite=1&offset=0&limit=3")
check("normal: lite/paginación con los mismos permisos (admin=false)", st == 200 and pgu.get("admin") is False and ids(pgu) == ids(up)[:3], (st, pgu.get("admin")))
st, h, rawg = u.raw("GET", "/api/uploads?lite=1", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO", "Accept-Encoding": "gzip"})
check("normal: gzip + lite devuelve las mismas pistas", st == 200 and ids(json.loads(gzip.decompress(rawg))) == ids(up), st)
if DEMO_PASS:
    st, pgd = d.api("GET", "/api/uploads?lite=1&offset=0&limit=3")
    check("demo: lite/paginación con admin=false y mismas pistas", st == 200 and pgd.get("admin") is False and ids(pgd) == ids(up)[:3], (st, pgd.get("admin")))
    st, h, rawd = d.raw("GET", "/api/uploads?lite=1", headers={"Accept": "application/json", "X-Requested-With": "SpotyOFO", "Accept-Encoding": "gzip"})
    check("demo: gzip + lite devuelve las mismas pistas", st == 200 and ids(json.loads(gzip.decompress(rawd))) == ids(up), st)
before = total_plays(admin)
admin.api("GET", "/api/uploads?lite=1&limit=50")
check("pedir la lista ligera no suma reproducciones", total_plays(admin) == before, "")

# ---------- portadas: carga diferida y caché ----------
st, h, body = admin.raw("GET", "/static/js/ui.js?v=x")
uijs = body.decode("utf-8", "replace")
check("ui.js: portadas con loading lazy/eager y decoding async", 'decoding="async"' in uijs and 'loading="${eager ? "eager" : "lazy"}"' in uijs and "function coverImg" in uijs, "")
check("ui.js: ya no quedan <img> de portadas sin atributos de carga", len(re.findall(r"<img src=", uijs)) == 1, len(re.findall(r"<img src=", uijs)))
check("ui.js: portadas con tamaño reservado (width/height) en filas y cola", all(f"size: {n}" in uijs for n in (36, 42, 48)), "")
st, home = admin.api("GET", "/api/home")
hashed = next((a["cover_url"] for a in home.get("albums", []) if re.match(r"^/static/covers/user/[0-9a-f]{16}\.(jpg|png|webp)$", a["cover_url"])), None)
if hashed is None:
    hashed = next((t["cover_url"] for t in tracks if re.match(r"^/static/covers/user/[0-9a-f]{16}\.(jpg|png|webp)$", t["cover_url"])), None)
if hashed:
    st, h, body = admin.raw("GET", hashed)
    check("portada con hash: 200 y Cache-Control private, una semana, immutable", st == 200 and h.get("Cache-Control") == "private, max-age=604800, immutable", (st, h.get("Cache-Control")))
    check("portada con hash: sin «Vary: Cookie» (la caché del navegador se reutiliza)", "cookie" not in h.get("Vary", "").lower(), h.get("Vary"))
    check("portada con hash: no renueva la cookie de sesión", "Set-Cookie" not in h and "set-cookie" not in {k.lower() for k in h}, [k for k in h if k.lower() == "set-cookie"])
    st, h2, body2 = admin.raw("GET", hashed, headers={"If-None-Match": next((v for k, v in h.items() if k.lower() == "etag"), "")})
    check("portada con hash: If-None-Match -> 304", st == 304, st)
    st, h3, _ = anon.raw("GET", hashed)
    check("portada con hash: sin sesión sigue dando 401", st == 401, st)
else:
    print("(no hay portadas con hash en esta base: se omiten las pruebas de portada con hash)")
st, h, body = admin.raw("GET", "/static/covers/lista-1.svg")
check("portada de catálogo (svg): private, 1 h y Vary: Cookie como antes", st == 200 and h.get("Cache-Control") == "private, max-age=3600" and "cookie" in h.get("Vary", "").lower(), (st, h.get("Cache-Control"), h.get("Vary")))
st, h, _ = anon.raw("GET", "/static/covers/lista-1.svg")
check("portada de catálogo sin sesión: 401", st == 401, st)
st, h, _ = admin.raw("GET", "/api/health")
check("la API sigue renovando la sesión (Set-Cookie en /api/*)", any(k.lower() == "set-cookie" for k in h), list(h))
if DEMO_PASS:
    if hashed:
        st, h, _ = d.raw("GET", hashed)
        check("demo: ve las portadas con hash con las mismas cabeceras", st == 200 and h.get("Cache-Control") == "private, max-age=604800, immutable", (st, h.get("Cache-Control")))

# ---------- archivos estáticos ----------
st, h, body = anon.raw("GET", "/login")
st, h, body = admin.raw("GET", "/")
html = body.decode("utf-8", "replace")
m = re.search(r'/static/js/main\.js\?v=([0-9a-z]+)', html)
check("la página carga main.js con versión", st == 200 and bool(m), st)
if m:
    for name in ("main", "ui", "player"):
        st, h, body = admin.raw("GET", f"/static/js/{name}.js?v={m.group(1)}")
        check(f"{name}.js sirve con la versión {m.group(1)}", st == 200 and len(body) > 100, st)
    st, h, body = admin.raw("GET", f"/static/js/ui.js?v={m.group(1)}")
    js = body.decode("utf-8", "replace")
    check("ui.js incluye la acción play-uploads", "play-uploads-shuffle" in js and "Reproducir todas" in js, "")
    check("ui.js pide la lista por páginas y extiende la cola", "uploadsPage" in js and "extendQueue" in js and "UP_FIRST_PAGE" in js, "")
    st, h, body = admin.raw("GET", f"/static/js/player.js?v={m.group(1)}")
    check("player.js expone extendQueue", b"extendQueue" in body, "")
    st, h, body = admin.raw("GET", f"/static/js/api.js?v={m.group(1)}")
    check("api.js tiene uploadsPage", b"uploadsPage" in body, "")
    check("ui.js importa módulos con la versión nueva", "?v=20261007b" in js and "?v=20261007a" not in js and "?v=20260930g" not in js, "")
    check("el menú muestra la versión y Actualizar", 'id="user-version"' in html and "Versión" in html and 'id="user-update"' in html and ">Actualizar<" in html, "")
    check("el sitio se puede instalar desde el navegador", 'rel="manifest"' in html and "manifest.webmanifest" in html and 'id="user-install"' in html, "")

# ---------- limpieza ----------
st, data = admin.api("GET", "/api/admin/users")
for x in data.get("users", []):
    if x["username"] == "zzplay":
        st, res = admin.api("DELETE", f"/api/admin/users/{x['id']}")
        check("borrar la cuenta de prueba", st == 200, (st, res))

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)