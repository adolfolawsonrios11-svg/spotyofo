"""Pruebas del permiso «editar canciones» para cuentas que no son administrador. Solo biblioteca estándar.

Uso (app corriendo, administrador de prueba y cuenta demo):
  python tests_perm.py http://127.0.0.1:5050 --admin zzadmin --admin-pass CLAVE [--log RUTA_LOG] [--db-tamper]

Crea las cuentas zzeditor, zznormal y zzpendiente y 2 canciones «ZZ Permiso …»; al final las borra.
--log RUTA        además revisa que el registro de la app anote quién editó y quién dio/quitó el permiso.
--db-tamper       SOLO en una base de prueba: marca can_edit = 1 a la demo directamente en la base
                  (usa la conexión de config/.env) para comprobar que igual recibe 403; luego lo deja en 0.
"""

import argparse
import io
import os
import re
import secrets
import struct
import sys
import time
import wave

import config  # noqa: F401  (carga .env)
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--admin", default="zzadmin")
ap.add_argument("--admin-pass", default="Prueba-Admin-2026!")
ap.add_argument("--demo-user", default=os.environ.get("SPOTY_DEMO_USER", "demo"))
ap.add_argument("--log")
ap.add_argument("--db-tamper", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")
DEMO_PASS = os.environ.get("SPOTY_DEMO_PASSWORD", "")
PASS = "Clave-de-Permiso-2026"
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

    def api(self, method, path, body=None, headers=None, data=None, csrf=True):
        hdrs = {"X-Requested-With": "SpotyOFO", "Accept": "application/json"}
        if csrf:
            hdrs["X-CSRF-Token"] = self.csrf
        if body is not None:
            hdrs["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        hdrs.update(headers or {})
        st, h, raw = self.raw(method, path, data, hdrs)
        try:
            return st, json.loads(raw or b"{}")
        except ValueError:
            return st, {"_raw": raw[:200]}

    def multipart(self, path, filename, content, ctype):
        boundary = "----zz" + secrets.token_hex(8)
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode()
        return self.api("POST", path, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, data=body)

    def login(self, user, password):
        st, h, text = self.form("/login", {"username": user, "password": password})
        if st in (302, 303):
            code, me = self.api("GET", "/api/me")
            if code == 200:
                self.csrf = me["csrf"]
        return st, text


def wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", (b - 128) * 40) for b in secrets.token_bytes(4000)))
    return buf.getvalue()


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a1a8e0a40000000049454e44ae426082"
)


def users(admin):
    st, data = admin.api("GET", "/api/admin/users")
    return {u["username"]: u for u in data.get("users", [])}


def act(admin, user_id, action):
    return admin.api("POST", f"/api/admin/users/{user_id}/{action}")


admin = Client()
st, _ = admin.login(args.admin, args.admin_pass)
check("login del administrador de prueba", st in (302, 303), st)
st, me = admin.api("GET", "/api/me")
check("/api/me del admin: can_edit = true", me.get("can_edit") is True and me.get("admin") is True, me)
admin_id = me["user"]["id"]

# limpieza de una corrida anterior
for name, u in users(admin).items():
    if name in ("zzeditor", "zznormal", "zzpendiente"):
        admin.api("DELETE", f"/api/admin/users/{u['id']}")
st, srch = admin.api("GET", "/api/search?q=ZZ%20Permiso&all=1")
for t in srch.get("tracks", []):
    if t.get("source") == "user":
        admin.api("DELETE", f"/api/tracks/{t['id']}")
st, health0 = admin.api("GET", "/api/health")

ids = []
for name in ("ZZ Permiso - Uno.wav", "ZZ Permiso - Dos.wav"):
    st, data = admin.multipart("/api/uploads", name, wav_bytes(), "audio/wav")
    check(f"subir {name}", st == 201, (st, data))
    ids.append(data.get("id"))
t1, t2 = ids
st, up = admin.api("GET", "/api/uploads?limit=5")
album_id = next(t["album_id"] for t in up["tracks"] if t["id"] == t1)

# cuentas de prueba
for name in ("zzeditor", "zznormal", "zzpendiente"):
    Client().form("/register", {"username": name, "display_name": name.capitalize(), "password": PASS, "confirm": PASS})
u = users(admin)
check("se registraron las 3 cuentas de prueba", all(n in u for n in ("zzeditor", "zznormal", "zzpendiente")), list(u))
for name in ("zzeditor", "zznormal"):
    act(admin, u[name]["id"], "approve")
u = users(admin)
ed_id, no_id, pe_id = u["zzeditor"]["id"], u["zznormal"]["id"], u["zzpendiente"]["id"]
check("cuentas nuevas sin permiso (can_edit = false)", not u["zzeditor"]["can_edit"] and not u["zznormal"]["can_edit"], u["zzeditor"])

editor = Client()
st, _ = editor.login("zzeditor", PASS)
check("login de zzeditor", st in (302, 303), st)
normal = Client()
st, _ = normal.login("zznormal", PASS)
check("login de zznormal", st in (302, 303), st)

# ---------- antes del permiso ----------
st, me = editor.api("GET", "/api/me")
check("zzeditor sin permiso: /api/me can_edit = false", me.get("can_edit") is False and me.get("admin") is False, me)
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"title": "Antes del permiso"})
check("sin permiso: PATCH -> 403", st == 403, (st, res))

# ---------- el admin da el permiso ----------
st, res = act(admin, ed_id, "grant-edit")
check("admin da el permiso (grant-edit) -> 200", st == 200 and res.get("user", {}).get("can_edit") is True, (st, res))
check("la lista de cuentas muestra can_edit = true", users(admin)["zzeditor"]["can_edit"] is True, "")
st, res = act(admin, ed_id, "grant-edit")
check("darlo dos veces no falla (idempotente)", st == 200 and res["user"]["can_edit"] is True, st)
st, res = act(admin, admin_id, "grant-edit")
check("dar el permiso a un administrador -> 409 (ya puede)", st == 409, (st, res))
st, res = act(admin, pe_id, "grant-edit")
check("dar el permiso a una cuenta pendiente -> 409", st == 409 and users(admin)["zzpendiente"]["can_edit"] is False, (st, res))
st, res = act(admin, 999999999, "grant-edit")
check("dar el permiso a una cuenta inexistente -> 404", st == 404, st)
st, res = admin.api("POST", f"/api/admin/users/{ed_id}/grant-edit", csrf=False)
check("dar el permiso sin CSRF -> 403", st == 403, st)

# ---------- el editor (misma sesión, sin volver a entrar) ----------
st, me = editor.api("GET", "/api/me")
check("efecto inmediato: /api/me can_edit = true sin volver a entrar", me.get("can_edit") is True and me.get("admin") is False, me)
st, upl = editor.api("GET", "/api/uploads?limit=1")
check("/api/uploads: can_edit = true y admin = false para el editor", upl.get("can_edit") is True and upl.get("admin") is False, (upl.get("can_edit"), upl.get("admin")))
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"title": "Editada por el editor", "genre": "ZZ Género Permiso"})
check("editor: editar una canción -> 200", st == 200 and res["track"]["title"] == "Editada por el editor", (st, res))
st, res = editor.api("PATCH", "/api/tracks/batch", {"ids": [t1, t2], "changes": {"album": "ZZ Permiso Álbum", "year": 2020}})
check("editor: edición por lote -> 200", st == 200 and all(t["album"] == "ZZ Permiso Álbum" and t["year"] == 2020 for t in res.get("tracks", [])), (st, res))
album_id = res["tracks"][0]["album_id"] if st == 200 else album_id
st, res = editor.multipart(f"/api/albums/{album_id}/cover", "p.png", PNG_1PX, "image/png")
check("editor: cambiar portada -> 200", st == 200 and res.get("cover_url", "").endswith(".png"), (st, res))
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"title": ""})
check("editor: mismas validaciones (título vacío -> 400)", st == 400, st)
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"plays": 5})
check("editor: campo no permitido -> 400", st == 400, st)
st, res = editor.api("PATCH", "/api/tracks/999999999", {"title": "x"})
check("editor: pista inexistente -> 404", st == 404, st)
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"title": "sin csrf"}, csrf=False)
check("editor: sin CSRF -> 403", st == 403, st)

# lo que el editor NO puede hacer
st, _ = editor.api("DELETE", f"/api/tracks/{t2}")
check("editor: borrar una canción -> 403", st == 403, st)
st, _ = editor.api("GET", "/api/admin/users")
check("editor: ver cuentas -> 403", st == 403, st)
st, _ = editor.api("POST", f"/api/admin/users/{no_id}/grant-edit")
check("editor: dar el permiso a otro -> 403", st == 403 and users(admin)["zznormal"]["can_edit"] is False, st)
st, _ = editor.api("POST", f"/api/admin/users/{ed_id}/grant-edit")
check("editor: dárselo a sí mismo -> 403", st == 403, st)
st, _ = editor.api("POST", f"/api/admin/users/{pe_id}/approve")
check("editor: aprobar cuentas -> 403", st == 403, st)
st, _ = editor.api("POST", f"/api/admin/users/{no_id}/block")
check("editor: bloquear cuentas -> 403", st == 403, st)
st, _ = editor.api("DELETE", f"/api/admin/users/{no_id}")
check("editor: borrar cuentas -> 403", st == 403, st)
st, _ = editor.api("GET", "/api/equipo")
check("editor: carpeta del servidor (solo admin) -> 403", st == 403, st)
st, h, body = editor.raw("GET", "/")
st2, h2, body2 = editor.raw("GET", "/static/js/ui.js?v=x")
js = body2.decode("utf-8", "replace")
check("ui.js: menú Editar para canEdit(); «Eliminar archivo» solo para isAdmin()", 'track.source === "user" && canEdit() ? `${item("edit-track"' in js and 'track.source === "user" && isAdmin() ? item("delete-upload"' in js, "")
check("ui.js: botón «Editar varias» en Subir según can_edit (no solo admin)", "upl.can_edit = Boolean(first.can_edit" in js and "${upl.can_edit && count ?" in js, "")
check("ui.js: interruptor «Puede editar canciones» en Cuentas", "data-perm-edit" in js and "grant-edit" in js and "revoke-edit" in js, "")

# ---------- cuenta normal ----------
st, me = normal.api("GET", "/api/me")
check("usuario normal: /api/me can_edit = false", me.get("can_edit") is False, me)
st, upl = normal.api("GET", "/api/uploads?limit=1")
check("usuario normal: /api/uploads can_edit = false", upl.get("can_edit") is False, upl.get("can_edit"))
st, _ = normal.api("PATCH", f"/api/tracks/{t1}", {"title": "normal"})
check("usuario normal: editar -> 403", st == 403, st)
st, _ = normal.api("PATCH", "/api/tracks/batch", {"ids": [t1], "changes": {"genre": "normal"}})
check("usuario normal: lote -> 403", st == 403, st)
st, _ = normal.multipart(f"/api/albums/{album_id}/cover", "p.png", PNG_1PX, "image/png")
check("usuario normal: portada -> 403", st == 403, st)
st, _ = normal.api("POST", f"/api/admin/users/{no_id}/grant-edit")
check("usuario normal: darse el permiso -> 403", st == 403, st)

# ---------- demo ----------
demo_row = users(admin).get(args.demo_user)
if demo_row and demo_row.get("demo"):
    st, res = act(admin, demo_row["id"], "grant-edit")
    check("admin no puede dar el permiso a la demo -> 403", st == 403 and users(admin)[args.demo_user]["can_edit"] is False, (st, res))
    st, res = act(admin, demo_row["id"], "revoke-edit")
    check("quitar el permiso a la demo no falla (queda en false)", st == 200 and res["user"]["can_edit"] is False, st)
    if DEMO_PASS:
        demo = Client()
        st, _ = demo.login(args.demo_user, DEMO_PASS)
        check("login de la demo", st in (302, 303), st)
        st, me = demo.api("GET", "/api/me")
        check("demo: /api/me can_edit = false", me.get("can_edit") is False and me.get("demo") is True, me)
        st, _ = demo.api("PATCH", f"/api/tracks/{t1}", {"title": "demo"})
        check("demo: editar -> 403", st == 403, st)
        st, _ = demo.api("PATCH", "/api/tracks/batch", {"ids": [t1], "changes": {"genre": "demo"}})
        check("demo: lote -> 403", st == 403, st)
        if args.db_tamper:
            import db
            conn = db.connect()
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET can_edit = 1 WHERE id = %s AND is_demo = 1", (demo_row["id"],))
            try:
                st, me = demo.api("GET", "/api/me")
                check("demo con can_edit = 1 forzado en la base: /api/me sigue en false", me.get("can_edit") is False, me)
                st, _ = demo.api("PATCH", f"/api/tracks/{t1}", {"title": "demo forzada"})
                check("demo con can_edit = 1 forzado en la base: editar sigue en 403", st == 403, st)
                check("la lista de cuentas no muestra a la demo como editora", users(admin)[args.demo_user]["can_edit"] is False, "")
            finally:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET can_edit = 0 WHERE id = %s AND is_demo = 1", (demo_row["id"],))
                conn.close()
    else:
        print("(sin SPOTY_DEMO_PASSWORD: se omite el ingreso como demo)")
else:
    print("(no hay cuenta demo: se omiten sus pruebas)")

# ---------- quitar el permiso: efecto inmediato ----------
st, res = act(admin, ed_id, "revoke-edit")
check("admin quita el permiso (revoke-edit) -> 200", st == 200 and res["user"]["can_edit"] is False, (st, res))
st, res = editor.api("PATCH", f"/api/tracks/{t1}", {"title": "Después de quitarlo"})
check("efecto inmediato: la misma sesión del editor recibe 403", st == 403, (st, res))
st, res = editor.api("PATCH", "/api/tracks/batch", {"ids": [t1], "changes": {"genre": "x"}})
check("efecto inmediato: lote -> 403", st == 403, st)
st, res = editor.multipart(f"/api/albums/{album_id}/cover", "p.png", PNG_1PX, "image/png")
check("efecto inmediato: portada -> 403", st == 403, st)
st, me = editor.api("GET", "/api/me")
check("efecto inmediato: /api/me can_edit = false", me.get("can_edit") is False, me)
st, data = admin.api("GET", f"/api/albums/{album_id}")
check("la canción quedó con la última edición permitida", any(t["id"] == t1 and t["title"] == "Editada por el editor" for t in data.get("tracks", [])), [t["title"] for t in data.get("tracks", [])])

# ---------- límite por usuario ----------
act(admin, ed_id, "grant-edit")
codes = [editor.api("PATCH", f"/api/tracks/{t2}", {"track_number": 2})[0] for _ in range(65)]
check("límite por usuario: el editor recibe 429 tras 60 ediciones/min", 429 in codes, codes[-5:])
st, _ = admin.api("PATCH", f"/api/tracks/{t2}", {"track_number": 3})
check("el límite del editor no frena al admin (es por usuario)", st == 200, st)
act(admin, ed_id, "revoke-edit")

# ---------- registro (log) ----------
if args.log:
    time.sleep(0.5)
    text = open(args.log, encoding="utf-8", errors="replace").read()
    check("log: anota que el admin dio el permiso", re.search(rf"Permiso de edici.n: admin {re.escape(args.admin)} \(id {admin_id}\) lo dio a zzeditor \(id {ed_id}\)", text), "")
    check("log: anota que el admin quitó el permiso", re.search(rf"Permiso de edici.n: admin {re.escape(args.admin)} \(id {admin_id}\) lo quit. a zzeditor \(id {ed_id}\)", text), "")
    check("log: anota qué editor editó la canción", re.search(rf"Editor zzeditor \(id {ed_id}\) edit. la canci.n {t1}", text), "")
    check("log: anota qué editor editó por lote y la portada", re.search(rf"Editor zzeditor \(id {ed_id}\) edit. 2 canciones", text) and re.search(rf"Editor zzeditor \(id {ed_id}\) cambi. la portada", text), "")

# ---------- limpieza ----------
for track_id in ids:
    st, _ = admin.api("DELETE", f"/api/tracks/{track_id}")
    check(f"borrar pista de prueba {track_id}", st == 200, st)
for name in ("zzeditor", "zznormal", "zzpendiente"):
    st, _ = admin.api("DELETE", f"/api/admin/users/{users(admin)[name]['id']}")
    check(f"borrar cuenta {name}", st == 200, st)
st, health1 = admin.api("GET", "/api/health")
check("la biblioteca vuelve a la cantidad inicial", health1.get("tracks") == health0.get("tracks"), (health0, health1))

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
