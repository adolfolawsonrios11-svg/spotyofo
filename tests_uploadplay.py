"""Pruebas de «Reproducir todas» en Subir: /api/uploads y permisos (solo biblioteca estándar).

Uso (con la app corriendo, un administrador de prueba y la cuenta demo creada):
  set SPOTY_DEMO_PASSWORD=...        (o en .env)
  python tests_uploadplay.py http://127.0.0.1:5050 --admin zzadmin --admin-pass CLAVE

Crea una cuenta «zzplay» (y la borra al final). No cambia datos de otras cuentas.
La cuenta demo solo lee; quien reinicia sus datos es «python manage.py demo-reset».
"""

import argparse
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
check("normal: subir -> 403", st == 403, st)
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
    check("ui.js importa módulos con la versión nueva", "?v=20260930a" in js and "?v=20260928a" not in js, "")

# ---------- limpieza ----------
st, data = admin.api("GET", "/api/admin/users")
for x in data.get("users", []):
    if x["username"] == "zzplay":
        st, res = admin.api("DELETE", f"/api/admin/users/{x['id']}")
        check("borrar la cuenta de prueba", st == 200, (st, res))

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)