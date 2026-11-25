"""Pruebas de compartir (enlaces /s/<token>, «Compartidos conmigo») y de borrar solo las canciones propias.

Uso (app corriendo con la base de PRUEBA y la cuenta demo creada):
  python tests_share.py http://127.0.0.1:5050

Crea directamente en la base las cuentas zzsh_owner, zzsh_other, zzsh_admin y zzsh_pending (no hace falta la clave del
administrador real), sube canciones «ZZ Compartir …» y al final borra todo (cuentas, canciones, papelera de prueba).
Solo biblioteca estándar + los módulos del proyecto.
"""

import argparse
import io
import json
import os
import re
import secrets
import struct
import sys
import wave

import config  # noqa: F401  (carga .env)
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import accounts
import audiolite
import db
import uploads

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--demo-user", default=os.environ.get("SPOTY_DEMO_USER", "demo"))
args = ap.parse_args()
BASE = args.base.rstrip("/")
DEMO_PASS = os.environ.get("SPOTY_DEMO_PASSWORD", "")
PASS = "Clave-Compartir-2026!"
ROOT = Path(__file__).resolve().parent
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


class AnyCookie(http.cookiejar.DefaultCookiePolicy):
    def return_ok_secure(self, cookie, request):
        return True


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=AnyCookie())
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

    def multipart(self, path, filename, content, ctype="audio/wav"):
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


conn = db.connect()
db.init_schema(conn)
TEST_USERS = ("zzsh_owner", "zzsh_other", "zzsh_admin", "zzsh_pending", "zzsh_tmp")


def cleanup(admin_client=None):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username LIKE 'zzsh\\_%'")
        ids = [r["id"] for r in cur.fetchall()]
        cur.execute("SELECT id FROM tracks WHERE title LIKE 'ZZ Compartir%' AND source = 'user'")
        track_ids = [r["id"] for r in cur.fetchall()]
    for tid in track_ids:
        try:
            uploads.delete_upload(conn, tid, actor_id=0, is_admin=True, actor_name="zz-limpieza")
        except uploads.UploadError:
            pass
    with conn.cursor() as cur:
        for uid in ids:
            cur.execute("DELETE FROM playlists WHERE owner_id = %s", (uid,))
            cur.execute("DELETE FROM play_history WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    # papelera de prueba: solo fichas de las canciones «ZZ Compartir …»
    for meta in list(uploads.trash_list()):
        if str(meta.get("title", "")).startswith("ZZ Compartir"):
            for name in (meta.get("trash_file"), meta.get("ficha")):
                for base in (uploads.TRASH_DIR, uploads.TRASH_DIR / "restaurado"):
                    p = base / str(name)
                    if name and p.is_file():
                        p.unlink()
    done = uploads.TRASH_DIR / "restaurado"
    if done.is_dir():
        for p in list(done.iterdir()):
            if p.suffix != ".json":
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
            if str(data.get("title", "")).startswith("ZZ Compartir"):
                for mate in list(done.glob(p.stem + ".*")):  # ficha + audio restaurado
                    mate.unlink()
    # audios restaurados de prueba (sin ficha): borrar por nombre de archivo del hash
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shares WHERE owner_id NOT IN (SELECT id FROM users)")


cleanup()


def make_user(name, role="user", status="active"):
    return accounts.create_user(conn, name, name, PASS, role=role, status=status)


owner_id = make_user("zzsh_owner")
other_id = make_user("zzsh_other")
admin_id = make_user("zzsh_admin", role="admin")
pending_id = make_user("zzsh_pending", status="pending")

owner, other, admin, demo_c, anon = Client(), Client(), Client(), Client(), Client()
for client, name in ((owner, "zzsh_owner"), (other, "zzsh_other"), (admin, "zzsh_admin")):
    st, _ = client.login(name, PASS)
    check(f"login {name}", st in (302, 303), st)
if DEMO_PASS:
    st, _ = demo_c.login(args.demo_user, DEMO_PASS)
    check("login demo", st in (302, 303), st)
else:
    print("AVISO: falta SPOTY_DEMO_PASSWORD; se omiten las pruebas con la demo")

# ---------- subir: cualquier cuenta no demo; queda registrada la dueña ----------
audio = wav_bytes()
st, up = owner.multipart("/api/uploads", "ZZ Artista Share - ZZ Compartir Uno.wav", audio)
check("una cuenta normal puede subir (sin permiso de edición)", st == 201 and up.get("id"), (st, up))
tid = up.get("id")
check("la pista recién subida trae mine=true y can_delete=true", up.get("mine") is True and up.get("can_delete") is True, up)
with conn.cursor() as cur:
    cur.execute("SELECT uploaded_by, audio_file, album_id FROM tracks WHERE id = %s", (tid,))
    row = cur.fetchone()
check("tracks.uploaded_by = id de quien subió", row and row["uploaded_by"] == owner_id, row)
audio_file = row["audio_file"]
album_id = row["album_id"]
audio_path = ROOT / "static" / "audio" / audio_file
check("el archivo está en static/audio/user", audio_path.is_file(), audio_path)

st, data = other.api("GET", "/api/uploads?lite=1")
mine_other = [t for t in data.get("tracks", []) if t["id"] == tid]
check("otra cuenta ve la canción, sin permiso de borrarla", st == 200 and mine_other and mine_other[0]["can_delete"] is False and mine_other[0]["mine"] is False, mine_other)
st, data = admin.api("GET", "/api/uploads?lite=1")
adm = [t for t in data.get("tracks", []) if t["id"] == tid]
check("el administrador ve can_delete=true en toda subida", adm and adm[0]["can_delete"] is True, adm)
if DEMO_PASS:
    st, data = demo_c.api("GET", "/api/uploads?lite=1")
    dm = [t for t in data.get("tracks", []) if t["id"] == tid]
    check("la demo ve can_delete=false", dm and dm[0]["can_delete"] is False, dm)
    st, up_demo = demo_c.multipart("/api/uploads", "ZZ Demo - ZZ Compartir Demo.wav", wav_bytes())
    check("la demo no puede subir (403)", st == 403, (st, up_demo))

# catálogo: nadie lo borra, ni el administrador
with conn.cursor() as cur:
    cur.execute("SELECT id FROM tracks WHERE source = 'catalog' LIMIT 1")
    cat = cur.fetchone()
if cat:
    st, data = admin.api("DELETE", f"/api/tracks/{cat['id']}")
    check("canción del catálogo: ni el administrador (403)", st == 403, (st, data))
    st, data = owner.api("DELETE", f"/api/tracks/{cat['id']}")
    check("canción del catálogo: cuenta normal 403", st == 403, (st, data))

# ---------- borrar: propiedad en el servidor ----------
st, data = other.api("DELETE", f"/api/tracks/{tid}")
check("otra cuenta no borra la canción ajena (403)", st == 403, (st, data))
st, data = anon.api("DELETE", f"/api/tracks/{tid}", csrf=False)
check("sin sesión: 401", st == 401, (st, data))
st, data = owner.api("DELETE", f"/api/tracks/{tid}", csrf=False)
check("sin token CSRF: 403", st == 403, (st, data))
if DEMO_PASS:
    st, data = demo_c.api("DELETE", f"/api/tracks/{tid}")
    check("la demo nunca borra (403)", st == 403, (st, data))
    st, data = demo_c.api("DELETE", f"/api/tracks/{cat['id']}") if cat else (403, {})
    check("la demo no borra ni el catálogo (403)", st == 403, (st, data))
check("tras los intentos ajenos la canción sigue y su archivo también", audio_path.is_file() and owner.api("GET", f"/api/albums/{album_id}")[0] == 200)
st, data = owner.api("DELETE", "/api/tracks/99999999")
check("canción inexistente: 404", st == 404, (st, data))

# ---------- compartir: enlace ----------
st, sh = owner.api("POST", "/api/shares", {"kind": "track", "id": tid})
check("crear enlace de canción", st == 201 and len(sh.get("token", "")) == 22 and sh.get("path") == f"/s/{sh.get('token')}", (st, sh))
token = sh.get("token", "")
st, sh2 = owner.api("POST", "/api/shares", {"kind": "track", "id": tid})
check("el mismo enlace se reutiliza", st == 201 and sh2.get("token") == token, sh2)
st, h, body = other.raw("GET", f"/s/{token}")
check("/s/<token> con sesión redirige a la vista dentro de la app", st in (302, 303) and h.get("Location", "").endswith(f"/#/s/{token}"), (st, h.get("Location")))
st, data = other.api("GET", f"/api/shares/{token}")
check("otra cuenta resuelve el enlace (canción + quién compartió)", st == 200 and data.get("kind") == "track" and data["track"]["id"] == tid and data["from"]["username"] == "zzsh_owner" and data["mine"] is False, (st, data))
check("la pista compartida trae audio_url para reproducir", data.get("track", {}).get("audio_url", "").startswith("/static/audio/"), data.get("track"))
st, h, body = anon.raw("GET", f"/s/{token}")
check("sin sesión /s/<token> exige iniciar sesión y vuelve después", st in (302, 303) and "/login" in h.get("Location", "") and "next=" in h.get("Location", "") and token in h.get("Location", ""), (st, h.get("Location")))
st, data = anon.api("GET", f"/api/shares/{token}", csrf=False)
check("sin sesión la API del enlace da 401", st == 401, (st, data))
st, h, body = other.raw("GET", "/s/" + "A" * 22)
check("enlace inventado -> 404 (página)", st == 404 and "Este enlace ya no existe" in body.decode("utf-8", "replace"), st)
st, data = other.api("GET", "/api/shares/" + "B" * 22)
check("enlace inventado -> 404 (API)", st == 404, (st, data))
st, data = other.api("GET", "/api/shares/corto")
check("token de largo incorrecto -> 404", st == 404, (st, data))
if DEMO_PASS:
    st, data = demo_c.api("GET", f"/api/shares/{token}")
    check("la demo puede abrir un enlace", st == 200, (st, data))
    st, data = demo_c.api("POST", "/api/shares", {"kind": "track", "id": tid})
    check("la demo no puede crear enlaces (403)", st == 403, (st, data))

# validaciones
st, data = owner.api("POST", "/api/shares", {"kind": "playlist", "id": 1})
check("solo canciones y discos (400)", st == 400, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": "abc"})
check("id no numérico (400)", st == 400, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": 99999999})
check("compartir algo inexistente (404)", st == 404, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid}, csrf=False)
check("compartir sin CSRF (403)", st == 403, (st, data))
st, data = anon.api("POST", "/api/shares", {"kind": "track", "id": tid}, csrf=False)
check("compartir sin sesión (401)", st == 401, (st, data))

# disco
st, shA = owner.api("POST", "/api/shares", {"kind": "album", "id": album_id})
check("crear enlace de disco", st == 201 and shA.get("kind") == "album", (st, shA))
st, data = other.api("GET", f"/api/shares/{shA.get('token')}")
check("el enlace del disco trae sus pistas", st == 200 and data.get("kind") == "album" and any(t["id"] == tid for t in data["album"]["tracks"]), (st, str(data)[:200]))

# ---------- compartir con una cuenta ----------
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": "zzsh_other"})
check("enviar a una cuenta", st == 201 and data.get("to", {}).get("username") == "zzsh_other" and data["token"] != token, (st, data))
direct = data.get("token", "")
st, inbox = other.api("GET", "/api/shares/inbox")
check("aparece en «Compartidos conmigo» como nuevo", st == 200 and inbox.get("unseen") == 1 and inbox["items"] and inbox["items"][0]["token"] == direct and inbox["items"][0]["unseen"] is True and inbox["items"][0]["from_username"] == "zzsh_owner", (st, inbox))
st, data = other.api("GET", f"/api/shares/{direct}")
st, inbox = other.api("GET", "/api/shares/inbox")
check("al abrirlo deja de estar nuevo", inbox.get("unseen") == 0 and inbox["items"][0]["unseen"] is False, inbox)
st, inb_owner = owner.api("GET", "/api/shares/inbox")
check("quien comparte no lo ve en su bandeja", inb_owner.get("items") == [], inb_owner)
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": "zzsh_nadie"})
check("usuario inexistente (404)", st == 404, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": "zzsh_owner"})
check("enviarse a uno mismo (400)", st == 400, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": "zzsh_pending"})
check("cuenta pendiente de aprobación (404)", st == 404, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": args.demo_user})
check("la demo no puede ser destinataria (404)", st == 404, (st, data))
st, data = owner.api("POST", "/api/shares", {"kind": "track", "id": tid, "to": "' OR 1=1 --"})
check("nombre con comillas (404, sin error)", st == 404, (st, data))

# ---------- revocar ----------
st, data = other.api("DELETE", f"/api/shares/{token}")
check("otra cuenta no revoca un enlace ajeno (403)", st == 403, (st, data))
st, data = other.api("DELETE", f"/api/shares/{direct}")
check("quien lo recibió puede quitarlo de su bandeja", st == 200, (st, data))
st, inbox = other.api("GET", "/api/shares/inbox")
check("la bandeja queda vacía", inbox.get("items") == [], inbox)
st, data = owner.api("DELETE", f"/api/shares/{token}")
check("la dueña revoca su enlace", st == 200, (st, data))
st, data = other.api("GET", f"/api/shares/{token}")
check("el enlace revocado da 404", st == 404, (st, data))
st, shB = owner.api("POST", "/api/shares", {"kind": "track", "id": tid})
check("un enlace nuevo tras revocar es otro token", st == 201 and shB["token"] != token, shB)
token = shB["token"]

# ---------- borrar: dueña ----------
st, data = other.api("POST", f"/api/likes/{tid}", {"shown_liked": False})
check("otra cuenta le da Me gusta", st == 200, (st, data))
st, pl = other.api("POST", "/api/playlists", {"name": "ZZ lista compartir"})
check("otra cuenta crea lista", st in (200, 201) and pl.get("id"), (st, pl))
st, data = other.api("POST", f"/api/playlists/{pl.get('id')}/tracks", {"track_id": tid})
check("y la agrega a su lista", st == 200, (st, data))
lite_path = audiolite.lite_candidates(audio_file)[0][1]
lite_path.parent.mkdir(parents=True, exist_ok=True)
lite_path.write_bytes(b"x" * 64)
check("(prep) copia liviana de prueba creada", lite_path.is_file())

st, data = owner.api("DELETE", f"/api/tracks/{tid}")
check("la dueña borra su canción", st == 200 and data.get("ok") is True, (st, data))
check("el archivo ya no está en static/audio/user", not audio_path.exists(), audio_path)
trash_items = [m for m in uploads.trash_list() if m.get("track_id") == tid and m.get("title") == "ZZ Compartir Uno"]
check("soft delete: archivo + ficha en la papelera", trash_items and trash_items[0]["archivo_existe"] and (uploads.TRASH_DIR / trash_items[0]["trash_file"]).is_file(), uploads.TRASH_DIR)
check("la ficha guarda quién subió y quién borró", trash_items and trash_items[0]["uploaded_by"] == owner_id and trash_items[0]["deleted_by"] == owner_id, trash_items[:1])
check("se borró la copia liviana", not lite_path.exists(), lite_path)
st, data = other.api("GET", "/api/likes")
check("sale de Me gusta de otras cuentas", st == 200 and all(t["id"] != tid for t in data), (st, str(data)[:120]))
st, data = other.api("GET", f"/api/playlists/{pl.get('id')}")
check("sale de las listas", st == 200 and all(t["id"] != tid for t in data.get("tracks", [])), data.get("tracks"))
st, data = other.api("GET", f"/api/shares/{token}")
check("sus enlaces compartidos dejan de abrir (404)", st == 404, (st, data))
st, data = other.api("GET", f"/api/shares/{shA['token']}")
check("el disco quedó vacío y su enlace también (404)", st == 404, (st, data))
st, data = other.api("GET", f"/api/audio/lite?ids={tid},{cat['id'] if cat else 1}")
live = {item["id"] for item in data.get("items", [])}
check("/api/audio/lite no lista ids borrados (contrato de la limpieza offline)", st == 200 and tid not in live, data)
st, h, raw = other.raw("GET", f"/static/audio/{audio_file}")
check("el audio borrado da 404", st == 404, st)
st, data = owner.api("DELETE", f"/api/tracks/{tid}")
check("borrar dos veces: 404", st == 404, (st, data))
st, again = owner.multipart("/api/uploads", "ZZ Artista Share - ZZ Compartir Uno.wav", audio)
check("tras borrar se puede volver a subir el mismo archivo", st == 201, (st, again))
if again.get("id"):
    owner.api("DELETE", f"/api/tracks/{again['id']}")

# ---------- restaurar desde la papelera ----------
audio2 = wav_bytes()
st, up2 = owner.multipart("/api/uploads", "ZZ Artista Share - ZZ Compartir Dos.wav", audio2)
tid2 = up2.get("id")
st, data = owner.api("DELETE", f"/api/tracks/{tid2}")
meta2 = [m for m in uploads.trash_list() if m.get("track_id") == tid2]
check("segunda canción en la papelera", st == 200 and meta2, (st, data))
if meta2:
    new_id = uploads.trash_restore(conn, meta2[0]["ficha"])
    with conn.cursor() as cur:
        cur.execute("SELECT uploaded_by, title FROM tracks WHERE id = %s", (new_id,))
        back = cur.fetchone()
    check("restaurar desde la papelera conserva dueña y título", back and back["uploaded_by"] == owner_id and back["title"] == "ZZ Compartir Dos", back)
    st, data = owner.api("DELETE", f"/api/tracks/{new_id}")
    check("la restaurada se puede volver a borrar", st == 200, (st, data))

# ---------- administrador ----------
st, up3 = other.multipart("/api/uploads", "ZZ Artista Otro - ZZ Compartir Tres.wav", wav_bytes())
tid3 = up3.get("id")
st, data = owner.api("DELETE", f"/api/tracks/{tid3}")
check("una cuenta no borra lo que subió otra (403)", st == 403, (st, data))
st, data = admin.api("DELETE", f"/api/tracks/{tid3}")
check("el administrador borra la de cualquiera", st == 200, (st, data))
st, up4 = owner.multipart("/api/uploads", "ZZ Artista Share - ZZ Compartir Cuatro.wav", wav_bytes())
tid4 = up4.get("id")
with conn.cursor() as cur:
    cur.execute("UPDATE tracks SET uploaded_by = NULL WHERE id = %s", (tid4,))
st, data = owner.api("DELETE", f"/api/tracks/{tid4}")
check("subida sin dueña (anterior al campo): la cuenta no puede borrarla (403)", st == 403, (st, data))
st, data = admin.api("GET", "/api/uploads?lite=1")
row4 = [t for t in data.get("tracks", []) if t["id"] == tid4]
st2, data2 = owner.api("GET", "/api/uploads?lite=1")
row4o = [t for t in data2.get("tracks", []) if t["id"] == tid4]
check("sin dueña: el administrador sí puede, la cuenta no", row4 and row4[0]["can_delete"] is True and row4o and row4o[0]["can_delete"] is False, (row4, row4o))
st, data = admin.api("DELETE", f"/api/tracks/{tid4}")
check("el administrador la borra", st == 200, (st, data))

# ---------- borrar una cuenta no borra sus canciones ----------
tmp_id = make_user("zzsh_tmp")
tmp = Client()
tmp.login("zzsh_tmp", PASS)
st, up5 = tmp.multipart("/api/uploads", "ZZ Artista Tmp - ZZ Compartir Cinco.wav", wav_bytes())
tid5 = up5.get("id")
st, data = admin.api("DELETE", f"/api/admin/users/{tmp_id}")
check("el administrador borra la cuenta zzsh_tmp", st == 200, (st, data))
with conn.cursor() as cur:
    cur.execute("SELECT uploaded_by FROM tracks WHERE id = %s", (tid5,))
    r5 = cur.fetchone()
check("sus canciones siguen, sin dueña (solo el administrador podrá borrarlas)", r5 and r5["uploaded_by"] is None, r5)
admin.api("DELETE", f"/api/tracks/{tid5}")

# ---------- interfaz (estático) ----------
ui = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")
apijs = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
off = (ROOT / "static/js/offline.js").read_text(encoding="utf-8")
css = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
check("ui: botón Compartir en pista (menú) y disco, no para la demo", 'item("share-track"' in ui and 'shareButton("album"' in ui and "if (isDemo()) return \"\"" in ui)
check("ui: Web Share API con respaldo a copiar", "navigator.share(" in ui and "copyText(" in ui and "navigator.clipboard.writeText" in ui)
check("ui: ruta #/s/<token> y #/shared", 'name === "s" && arg' in ui and 'name === "shared"' in ui)
check("ui: borrar solo si el servidor marcó can_delete", "track.can_delete" in ui and "isAdmin() ? item(\"delete-upload\"" not in ui)
check("ui: confirmación antes de borrar y limpia la descarga local", "confirmDelete(track)" in ui and "removeDownload(track.id)" in ui)
check("offline: pruneDeleted limpia descargas de pistas borradas", "export async function pruneDeleted" in off and "pruneDeleted" in ui and "api.audioLite" in ui)
check("api: createShare / share / revokeShare / sharedInbox", all(k in apijs for k in ("createShare", "revokeShare", "sharedInbox", "audioLite")))
check("css: estilos de compartir", all(k in css for k in (".share-row", ".share-link", ".side-badge", ".lib-card.shared")))

cleanup()
with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE username LIKE 'zzsh\\_%'")
    left_users = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE title LIKE 'ZZ Compartir%'")
    left_tracks = cur.fetchone()["n"]
check("limpieza: sin cuentas ni canciones de prueba", left_users == 0 and left_tracks == 0, (left_users, left_tracks))
print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
