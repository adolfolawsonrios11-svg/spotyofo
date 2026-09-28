"""Pruebas de la cuenta demo de SpotyOFO (solo biblioteca estándar).

Uso (con la app corriendo y la demo creada con «python manage.py demo-reset»):
  set SPOTY_DEMO_PASSWORD=...        (o en .env)
  python tests_demo.py http://127.0.0.1:5050 [--button]

Solo cambia datos de la cuenta demo (Me gusta, listas, historial). Al final conviene
volver a correr «python manage.py demo-reset» para dejarla en su estado inicial.
--button: además prueba el botón «Probar demo» (requiere SPOTY_DEMO_ENABLED=1).
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
ap.add_argument("--demo-user", default=os.environ.get("SPOTY_DEMO_USER", "demo"))
ap.add_argument("--button", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")
PASSWORD = os.environ.get("SPOTY_DEMO_PASSWORD", "")
if not PASSWORD:
    sys.exit("Falta SPOTY_DEMO_PASSWORD.")
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

    def page(self, path):
        st, h, body = self.raw("GET", path)
        text = body.decode("utf-8", "replace")
        m = re.search(r'name="csrf_token" value="([^"]+)"', text)
        return st, h, text, (m.group(1) if m else "")

    def form(self, path, fields, page=None):
        st, h, text, token = self.page(page or path)
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}
        st, h, body = self.raw("POST", path, urllib.parse.urlencode({"csrf_token": token, **fields}).encode(), headers)
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

    def refresh(self):
        st, data = self.api("GET", "/api/me")
        if st == 200:
            self.csrf = data["csrf"]
        return st, data

    def login(self, user, password):
        st, h, text = self.form("/login", {"username": user, "password": password})
        if st in (302, 303):
            self.refresh()
        return st, h, text


def err(text):
    m = re.search(r'class="auth-msg is-error"[^>]*>([^<]+)<', text)
    return m.group(1) if m else ""


# ---------- ingreso y bloqueo ----------
c = Client()
for i in range(6):
    st, h, text = c.form("/login", {"username": args.demo_user, "password": f"incorrecta-{i}-xx"})
check("6 contraseñas malas -> 401 (sin bloqueo de cuenta)", st == 401 and "bloqueada" not in err(text), (st, err(text)))
st, h, text = c.login(args.demo_user, PASSWORD)
check("login demo tras intentos fallidos", st in (302, 303) and h.get("Location", "").endswith("/"), (st, err(text)))
st, me = c.refresh()
check("/api/me: demo=true, admin=false", st == 200 and me.get("demo") is True and me.get("admin") is False and me["user"]["demo"] is True, me)
check("demo sin cambio de contraseña obligatorio", not me["user"]["must_change_password"], me)
uid = me["user"]["id"]

# ---------- restricciones (servidor) ----------
st, h, text, token = c.page("/account/password")
check("GET /account/password: aviso demo y sin formulario activo", st == 200 and "Cuenta demo" in text and 'action="/account/password"' not in text, st)
headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}
body = urllib.parse.urlencode({"csrf_token": token, "current": PASSWORD, "password": "Otra-Clave-Nueva-99", "confirm": "Otra-Clave-Nueva-99"}).encode()
st, h, raw = c.raw("POST", "/account/password", body, headers)
check("POST /account/password -> 403", st == 403, st)
st, _ = c.api("GET", "/api/admin/users")
check("GET /api/admin/users -> 403", st == 403, st)
st, _ = c.api("POST", f"/api/admin/users/{uid}/reset-password")
check("POST /api/admin/users/<id>/reset-password -> 403", st == 403, st)
st, _ = c.api("POST", "/api/admin/users/1/block")
check("POST /api/admin/users/1/block -> 403", st == 403, st)
st, _ = c.api("DELETE", f"/api/admin/users/{uid}")
check("DELETE /api/admin/users/<propio id> -> 403", st == 403, st)
boundary = "----zzdemo"
multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.mp3\"\r\n"
             f"Content-Type: audio/mpeg\r\n\r\nID3\r\n--{boundary}--\r\n").encode()
st, _ = c.api("POST", "/api/uploads", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, data=multipart)
check("POST /api/uploads -> 403", st == 403, st)
st, _ = c.api("DELETE", "/api/tracks/1")
check("DELETE /api/tracks/1 -> 403", st == 403, st)
st, _ = c.api("GET", "/api/equipo")
check("GET /api/equipo -> 403", st == 403, st)
st, data = c.api("GET", "/api/uploads")
check("GET /api/uploads: admin=false", st == 200 and data.get("admin") is False, st)
st, h, text = c.login(args.demo_user, PASSWORD)
check("la contraseña sigue igual después de los intentos", st in (302, 303), (st, err(text)))

# ---------- experiencia normal ----------
st, home = c.api("GET", "/api/home")
check("home 200", st == 200, st)
mine = [p for p in home.get("playlists", []) if not p["editorial"]]
check("lista de muestra «Demo – Lo mejor» presente", any(p["name"] == "Demo – Lo mejor" for p in mine), [p["name"] for p in mine])
check("historial de muestra en «recientes»", len(home.get("recent", [])) >= 1, len(home.get("recent", [])))
st, likes = c.api("GET", "/api/likes")
check("Me gusta de muestra", st == 200 and len(likes) >= 1, len(likes) if isinstance(likes, list) else likes)
st, found = c.api("GET", "/api/search?q=a")
check("buscar", st == 200 and "tracks" in found, st)
st, gl = c.api("GET", "/api/genres")
check("géneros", st == 200 and "genres" in gl, st)
tracks = found.get("tracks") or []
check("hay pistas para probar", len(tracks) >= 2, len(tracks))
t1 = tracks[0]
st, res = c.api("POST", f"/api/likes/{t1['id']}", {"shown_liked": t1["liked"]})
check("Me gusta (alternar)", st == 200 and res.get("liked") is (not t1["liked"]), res)
st, pl = c.api("POST", "/api/playlists", {"name": "ZZ demo prueba"})
check("crear lista", st == 201, pl)
pid = pl.get("id")
st, res = c.api("POST", f"/api/playlists/{pid}/tracks", {"track_id": tracks[1]["id"]})
check("agregar a la lista", st == 200, res)
st, pd = c.api("GET", f"/api/playlists/{pid}")
check("la lista tiene la pista", st == 200 and len(pd.get("tracks", [])) == 1, pd)
st, album = c.api("GET", f"/api/albums/{t1['album_id']}")
before = next((t["plays"] for t in album.get("tracks", []) if t["id"] == t1["id"]), None)
st, _ = c.api("POST", "/api/plays", {"track_id": t1["id"]})
check("registrar reproducción", st == 200, st)
st, album = c.api("GET", f"/api/albums/{t1['album_id']}")
after = next((t["plays"] for t in album.get("tracks", []) if t["id"] == t1["id"]), None)
check("la demo no suma al contador global de reproducciones", before is not None and before == after, (before, after))
st, home = c.api("GET", "/api/home")
check("la reproducción aparece en recientes", st == 200 and home["recent"] and home["recent"][0]["id"] == t1["id"], [t["id"] for t in home.get("recent", [])])

# ---------- tope de listas ----------
st, data = c.api("GET", "/api/playlists/mine")
have = len(data)
last = None
for i in range(max(0, 20 - have) + 1):
    last = c.api("POST", "/api/playlists", {"name": f"ZZ tope {i}"})
check("tope de 20 listas para la demo -> 403", last[0] == 403 and "20" in last[1].get("error", ""), last)

# ---------- botón «Probar demo» ----------
anon = Client()
st, h, text, token = anon.page("/login")
if args.button:
    check("login muestra «Probar demo»", 'action="/login/demo"' in text, st)
    st, h, text = anon.form("/login/demo", {}, page="/login")
    check("«Probar demo» entra como demo", st in (302, 303) and h.get("Location", "").endswith("/"), (st, err(text)))
    st, me2 = anon.refresh()
    check("sesión del botón es la demo", st == 200 and me2.get("demo") is True, me2)
    bad = Client()
    st, h, raw = bad.raw("POST", "/login/demo", b"csrf_token=x", {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE})
    check("«Probar demo» sin CSRF válido -> rechazado", st == 400, st)
else:
    check("login sin «Probar demo» (SPOTY_DEMO_ENABLED apagado)", 'action="/login/demo"' not in text, st)
    st, h, raw = anon.raw("POST", "/login/demo", urllib.parse.urlencode({"csrf_token": token}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE})
    check("POST /login/demo apagado -> redirige a /login", st in (302, 303) and "/login" in h.get("Location", ""), st)

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
