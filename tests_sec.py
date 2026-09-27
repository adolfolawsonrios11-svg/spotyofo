"""Pruebas de seguridad y cuentas de SpotyOFO (solo biblioteca estándar).

Uso:
  python tests_sec.py BASE --setup-pin PIN          (sin administrador: prueba /setup y lo crea)
  python tests_sec.py BASE --admin USER --admin-pass PASS   (administrador de prueba ya creado)
Crea cuentas «zz…» y listas «ZZ …»; al final las borra (menos el administrador de prueba).
"""

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--setup-pin")
ap.add_argument("--setup-code-file", help="modo sin PIN: ruta a instance/setup_code.txt de la instancia de prueba")
ap.add_argument("--admin", default="zzadmin")
ap.add_argument("--admin-pass", default="Prueba-Admin-2026!")
ap.add_argument("--skip-ratelimit", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")
PASS_A = "Clave-de-Ana-2026"
PASS_B = "Clave-de-Beto-2026"
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

    def form(self, path, fields, origin=True):
        st, h, text, token = self.page(path)
        fields = {"csrf_token": token, **fields}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin:
            headers["Origin"] = BASE
        st, h, body = self.raw("POST", path, urllib.parse.urlencode(fields).encode(), headers)
        return st, h, body.decode("utf-8", "replace")

    def api(self, method, path, body=None, csrf=True, extra=None):
        headers = {"X-Requested-With": "SpotyOFO", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(extra or {})
        st, h, raw = self.raw(method, path, json.dumps(body).encode() if body is not None else None, headers)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {"_raw": raw[:200]}
        return st, data, h

    def login(self, user, password, next_path=None):
        fields = {"username": user, "password": password}
        if next_path:
            fields["next"] = next_path
        st, h, text = self.form("/login", fields)
        if st in (302, 303):
            me = self.api("GET", "/api/me")
            if me[0] == 200:
                self.csrf = me[1]["csrf"]
        return st, h, text

    def cookie(self):
        return {c.name: c.value for c in self.jar}.get("spotyofo_session")


def err(text):
    m = re.search(r'class="auth-msg is-error"[^>]*>([^<]+)<', text)
    return m.group(1) if m else ""


anon = Client()
st, h, _ = anon.raw("GET", "/api/health")
check("health público", st == 200)
st, h, _ = anon.raw("GET", "/api/home")
check("API sin sesión -> 401", st == 401)
st, h, _ = anon.raw("GET", "/")
check("/ sin sesión -> redirección", st in (302, 303) and ("/login" in h.get("Location", "") or "/setup" in h.get("Location", "")), h.get("Location"))

admin = Client()
if args.setup_code_file:
    st, h, text, tok = admin.page("/setup")
    check("setup (sin PIN) pide código", st == 200 and "Código de configuración" in text, st)
    args.setup_pin = open(args.setup_code_file).read().strip()
    st, h, text, tok = admin.page("/setup")
if args.setup_pin:
    st, h, text, tok = admin.page("/setup")
    check("setup visible sin admin", st == 200 and ("PIN actual" in text or "Código de configuración" in text), st)
    st, h, text = admin.form("/setup", {"proof": "000000", "username": args.admin, "password": args.admin_pass, "confirm": args.admin_pass})
    check("setup con PIN malo -> rechazado", st == 401 and "no es correcto" in err(text), (st, err(text)))
    st, h, text = admin.form("/setup", {"proof": args.setup_pin, "username": args.admin, "password": "corta", "confirm": "corta"})
    check("setup contraseña corta -> 400", st == 400 and "al menos" in err(text), (st, err(text)))
    st, h, text = admin.form("/setup", {"proof": args.setup_pin, "username": args.admin, "password": args.admin_pass, "confirm": args.admin_pass}, origin=False)
    check("setup crea admin -> redirección a /", st in (302, 303) and h.get("Location", "").endswith("/"), (st, err(text)))
    st, data, _ = admin.api("GET", "/api/me")
    admin.csrf = data.get("csrf", "")
    check("admin con sesión y rol admin", st == 200 and data.get("admin") is True, data)
    st, h, text, _ = Client().page("/setup")
    check("setup desaparece cuando hay admin", st in (302, 303) and "/login" in h.get("Location", ""), st)
else:
    st, h, text = admin.login(args.admin, args.admin_pass)
    check("login admin de prueba", st in (302, 303), (st, err(text)))

st, data, _ = admin.api("GET", "/api/playlists")
admin_lists = [p for p in data if not p["editorial"]]
editorial = [p for p in data if p["editorial"]]
check("editorial retirada para todas las cuentas", editorial == [], [p["name"] for p in editorial])
st, data, _ = admin.api("GET", "/api/likes")
admin_likes = len(data)
check("admin lee sus Me gusta", st == 200, st)

# ---- registro ----
a = Client()
st, h, text = a.form("/register", {"username": "zzana", "display_name": "Ana Prueba", "password": PASS_A, "confirm": PASS_A})
check("registro Ana -> pendiente", st == 200 and "pendiente" in text, (st, err(text)))
b = Client()
st, h, text = b.form("/register", {"username": "zzbeto", "display_name": "Beto", "password": PASS_B, "confirm": PASS_B})
check("registro Beto -> pendiente", st == 200 and "pendiente" in text, (st, err(text)))
st, h, text = Client().form("/register", {"username": "zzana", "password": PASS_A, "confirm": PASS_A})
check("usuario repetido -> 409", st == 409 and "en uso" in err(text), (st, err(text)))
st, h, text = Client().form("/register", {"username": "ZZ mal!", "password": PASS_A, "confirm": PASS_A})
check("usuario inválido -> 400", st == 400, (st, err(text)))
st, h, text = Client().form("/register", {"username": "zzsincsrf", "password": PASS_A, "confirm": PASS_A, "csrf_token": "x"})
check("registro con CSRF falso -> 400", st == 400 and "expiró" in err(text), (st, err(text)))
st, h, text = a.login("zzana", PASS_A)
check("pendiente no puede entrar", st == 403 and "aprobación" in err(text), (st, err(text)))

# ---- administración ----
st, data, _ = admin.api("GET", "/api/admin/users")
users = {u["username"]: u for u in data["users"]}
check("admin ve pendientes con contador", data["pending"] >= 2 and users["zzana"]["status"] == "pending", data.get("pending"))
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzana']['id']}/approve", csrf=False)
check("aprobar sin CSRF -> 403", st == 403, (st, data))
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzana']['id']}/approve")
check("aprobar Ana", st == 200 and data["user"]["status"] == "active", (st, data))
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzbeto']['id']}/reject")
check("rechazar Beto", st == 200 and data["user"]["status"] == "rejected", (st, data))
st, h, text = b.login("zzbeto", PASS_B)
check("rechazado no entra", st == 403 and "rechazada" in err(text), (st, err(text)))
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzbeto']['id']}/approve")
check("aprobar Beto después", st == 200 and data["user"]["status"] == "active", (st, data))

# ---- ingreso y sesión ----
pre = a.cookie()
st, h, text = a.login("zzana", PASS_A, next_path="//evil.example/x")
check("login Ana", st in (302, 303), (st, err(text)))
check("next externo ignorado", h.get("Location", "").rstrip("/") in ("", BASE) or h.get("Location") == "/", h.get("Location"))
check("cookie de sesión nueva al entrar", a.cookie() and a.cookie() != pre)
setcookie = h.get("Set-Cookie", "")
check("cookie HttpOnly y SameSite=Lax", "HttpOnly" in setcookie and "SameSite=Lax" in setcookie, setcookie)
st, me, _ = a.api("GET", "/api/me")
check("Ana es usuaria normal", st == 200 and me["admin"] is False and me["user"]["username"] == "zzana", me)
st, h, text = b.login("zzbeto", PASS_B)
check("login Beto", st in (302, 303), (st, err(text)))

# ---- aislamiento por usuario (IDOR) ----
st, data, _ = a.api("GET", "/api/playlists")
check("Ana no ve listas del admin", all(p["editorial"] for p in data), [p["name"] for p in data if not p["editorial"]])
st, home, _ = a.api("GET", "/api/home")
check("inicio de Ana sin listas ajenas ni historial ajeno", all(p["editorial"] for p in home["playlists"]) and home["recent"] == [], (len(home["recent"]),))
track_ids = [t["id"] for t in home["popular"]]
for pl in editorial:
    if len(track_ids) >= 2:
        break
    track_ids += [t["id"] for t in a.api("GET", f"/api/playlists/{pl['id']}")[1]["tracks"] if t["id"] not in track_ids]
t1, t2 = track_ids[0], track_ids[1]
st, data, _ = a.api("POST", "/api/playlists", {"name": "ZZ Lista de Ana", "track_id": t1})
ana_list = data.get("id")
check("Ana crea su lista con canción", st == 201 and data["result"]["added"] == 1, (st, data))
st, data, _ = b.api("GET", f"/api/playlists/{ana_list}")
check("Beto no puede ver la lista de Ana (404)", st == 404, st)
for method, path, body in [
    ("POST", f"/api/playlists/{ana_list}/tracks", {"track_id": t2}),
    ("POST", f"/api/playlists/{ana_list}/tracks/remove", {"keys": [f"t:{t1}"]}),
    ("DELETE", f"/api/playlists/{ana_list}/tracks/{t1}", None),
    ("PUT", f"/api/playlists/{ana_list}/order", {"keys": [f"t:{t1}"]}),
    ("PATCH", f"/api/playlists/{ana_list}", {"name": "hackeada"}),
    ("DELETE", f"/api/playlists/{ana_list}", None),
]:
    st, data, _ = b.api(method, path, body)
    check(f"Beto {method} {path.split(str(ana_list))[-1] or '/'} lista ajena -> 404", st == 404, (st, data))
st, data, _ = b.api("POST", "/api/playlists", {"name": "ZZ Copia", "source": {"type": "playlist", "id": ana_list}})
check("Beto no copia lista ajena como origen (404)", st == 404, (st, data))
st, data, _ = b.api("GET", "/api/playlists/mine")
check("selector de Beto vacío", st == 200 and data == [], data)
st, data, _ = b.api("GET", "/api/search?q=ZZ")
check("buscar no muestra listas ajenas", all(p["editorial"] for p in data["playlists"]), data["playlists"])
if admin_lists:
    st, data, _ = a.api("GET", f"/api/playlists/{admin_lists[0]['id']}")
    check("Ana no ve la lista migrada del admin (404)", st == 404, st)
if editorial:
    st, data, _ = a.api("POST", f"/api/playlists/{editorial[0]['id']}/tracks", {"track_id": t2})
    check("editorial protegida (403)", st == 403, (st, data))
st, data, _ = a.api("POST", f"/api/likes/{t2}", {"shown_liked": False})
check("Ana marca Me gusta", st == 200 and data["liked"] is True, data)
st, data, _ = a.api("GET", "/api/likes")
check("Me gusta de Ana solo con su canción", [t["id"] for t in data] == [t2], [t["id"] for t in data])
st, data, _ = b.api("GET", "/api/likes")
check("Beto no ve Me gusta de Ana", data == [], data)
st, data, _ = admin.api("GET", "/api/likes")
check("Me gusta del admin intactos", len(data) == admin_likes, (len(data), admin_likes))
st, data, _ = a.api("POST", "/api/plays", {"track_id": t1})
check("Ana registra reproducción", st == 200)
st, home, _ = a.api("GET", "/api/home")
check("historial de Ana propio", [t["id"] for t in home["recent"]] == [t1], [t["id"] for t in home["recent"]])
st, home, _ = b.api("GET", "/api/home")
check("Beto no ve historial de Ana", home["recent"] == [], home["recent"])

# ---- permisos de administrador ----
for method, path, body in [
    ("GET", "/api/admin/users", None),
    ("POST", f"/api/admin/users/{users['zzbeto']['id']}/block", None),
    ("DELETE", f"/api/tracks/{t1}", None),
    ("GET", "/api/equipo", None),
    ("GET", "/api/equipo/audio?path=x.mp3", None),
    ("POST", "/api/equipo/likes", {"path": "x"}),
]:
    st, data, _ = a.api(method, path, body)
    check(f"usuaria normal {method} {path} -> 403", st == 403, (st, data))
st, h, raw = a.raw("POST", "/api/uploads", b"--x--", {"X-Requested-With": "SpotyOFO", "X-CSRF-Token": a.csrf, "Content-Type": "multipart/form-data; boundary=x"})
check("usuaria normal no sube (403)", st == 403, st)
st, data, _ = a.api("POST", f"/api/playlists/{ana_list}/tracks", {"keys": ["d:Musica/x.mp3"]})
check("archivos del servidor solo admin (403)", st == 403, (st, data))

# ---- CSRF y origen ----
st, data, _ = a.api("POST", "/api/playlists", {"name": "ZZ sin token"}, csrf=False)
check("POST sin token CSRF -> 403", st == 403, st)
st, data, _ = a.api("POST", "/api/playlists", {"name": "ZZ token malo"}, extra={"X-CSRF-Token": "malo"})
check("POST con token falso -> 403", st == 403, st)
st, data, _ = a.api("POST", "/api/playlists", {"name": "ZZ origen"}, extra={"Origin": "https://evil.example"})
check("POST desde otro origen -> 403", st == 403, st)
st, data, _ = a.api("POST", "/api/playlists", {"name": "ZZ sitio"}, extra={"Sec-Fetch-Site": "cross-site"})
check("POST cross-site -> 403", st == 403, st)

# ---- cabeceras y errores ----
st, h, body = a.raw("GET", "/")
csp = h.get("Content-Security-Policy", "")
check("CSP con frame-ancestors none", "frame-ancestors 'none'" in csp and "script-src 'self'" in csp, csp)
check("nosniff + Referrer-Policy + X-Frame-Options", h.get("X-Content-Type-Options") == "nosniff" and h.get("Referrer-Policy") and h.get("X-Frame-Options") == "DENY", h)
st, h, body = a.raw("GET", "/api/home")
check("API con Cache-Control no-store", h.get("Cache-Control") == "no-store", h.get("Cache-Control"))
cover = home["popular"][0]["cover_url"] if home.get("popular") else "/static/covers/lista-1.svg"
st, h, body = a.raw("GET", cover)
check("portada con sesión: privada", st == 200 and h.get("Cache-Control", "").startswith("private"), (st, h.get("Cache-Control")))
st, h, body = Client().raw("GET", cover)
check("portada sin sesión -> 401 no-store", st == 401 and h.get("Cache-Control") == "no-store", (st, h.get("Cache-Control")))
st, h, body = a.raw("GET", "/no-existe")
check("404 sin detalles internos", st == 404 and b"Traceback" not in body and b"werkzeug" not in body.lower(), st)
st, data, _ = a.api("GET", "/api/no-existe")
check("404 API en JSON", st == 404 and "error" in data, (st, data))

# ---- bloqueo por intentos ----
c = Client()
for i in range(5):
    c.login("zzbeto", "mala-clave-000")
st, h, text = c.login("zzbeto", PASS_B)
check("5 fallos bloquean la cuenta aunque la clave sea buena", st == 429 and "bloqueada" in err(text), (st, err(text)))
st, h, text = Client().login("zznoexiste", "lo-que-sea-123")
check("usuario inexistente: mensaje genérico", st == 401 and err(text) == "Usuario o contraseña incorrectos.", (st, err(text)))

# ---- restablecer contraseña ----
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzbeto']['id']}/reset-password")
temp = data.get("temporary_password", "")
check("admin genera contraseña temporal", st == 200 and len(temp) >= 12, (st, data))
st, data, _ = b.api("GET", "/api/me")
check("sesión vieja de Beto se cerró", st == 401, st)
nb = Client()
st, h, text = nb.login("zzbeto", temp)
check("Beto entra con la temporal y va a cambiarla", st in (302, 303) and h.get("Location", "").endswith("/account/password"), (st, h.get("Location"), err(text)))
st, data, _ = nb.api("GET", "/api/home")
check("con temporal la API exige cambiarla (403)", st == 403 and data.get("needs") == "password", (st, data))
st, h, text = nb.form("/account/password", {"current": temp, "password": PASS_B + "x", "confirm": PASS_B + "x"})
check("Beto cambia su contraseña", st in (302, 303), (st, err(text)))
st, data, _ = nb.api("GET", "/api/home")
check("después del cambio, acceso normal", st == 200, st)

# ---- bloquear / desbloquear / logout ----
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzana']['id']}/block")
check("bloquear Ana", st == 200 and data["user"]["status"] == "blocked", (st, data))
st, data, _ = a.api("GET", "/api/me")
check("sesión de Ana deja de valer al bloquearla", st == 401, st)
st, h, text = Client().login("zzana", PASS_A)
check("bloqueada no entra", st == 403 and "bloqueada" in err(text), (st, err(text)))
st, data, _ = admin.api("POST", f"/api/admin/users/{users['zzana']['id']}/unblock")
check("desbloquear Ana", st == 200 and data["user"]["status"] == "active", (st, data))
st, data, _ = admin.api("POST", f"/api/admin/users/{data['user']['id']}/block".replace(str(data['user']['id']), str(admin.api('GET','/api/me')[1]['user']['id'])))
check("admin no se bloquea a sí mismo", st == 400, (st, data))
nb.csrf = nb.api("GET", "/api/me")[1].get("csrf", "")
st, data, h = nb.api("POST", "/api/auth/logout")
check("logout con Clear-Site-Data", st == 200 and "cache" in h.get("Clear-Site-Data", ""), h.get("Clear-Site-Data"))
st, data, _ = nb.api("GET", "/api/me")
check("después de logout -> 401", st == 401, st)

# ---- borrar cuentas de prueba (y sus listas) ----
a2 = Client()
a2.login("zzana", PASS_A)
st, data, _ = admin.api("GET", "/api/admin/users")
for u in data["users"]:
    if u["username"].startswith("zz") and u["username"] != args.admin:
        st, res, _ = admin.api("DELETE", f"/api/admin/users/{u['id']}")
        check(f"borrar {u['username']}", st == 200, (st, res))
st, data, _ = a2.api("GET", "/api/me")
check("cuenta borrada pierde la sesión", st == 401, st)
st, data, _ = admin.api("GET", f"/api/playlists/{ana_list}")
check("lista de Ana borrada con la cuenta", st == 404, st)

# ---- límite de registros por IP (al final: deja la IP limitada 1 h en memoria) ----
if not args.skip_ratelimit:
    codes = []
    for i in range(6):
        st, h, text = Client().form("/register", {"username": f"zzrl{i}", "password": PASS_A, "confirm": PASS_A})
        codes.append(st)
    check("límite de solicitudes de registro por IP", 429 in codes, codes)
    st, data, _ = admin.api("GET", "/api/admin/users")
    for u in data["users"]:
        if u["username"].startswith("zzrl"):
            admin.api("DELETE", f"/api/admin/users/{u['id']}")

print(f"\n{'OK' if ok else 'HAY FALLOS'}: {count} comprobaciones")
sys.exit(0 if ok else 1)
