"""Pruebas del editor de canciones (solo administradores). Solo biblioteca estándar.

Uso (con la app corriendo, un administrador de prueba y, si se quiere, la cuenta demo):
  set SPOTY_DEMO_PASSWORD=...        (o en .env)
  python tests_edit.py http://127.0.0.1:5050 --admin zzadmin --admin-pass CLAVE [--ratelimit]

Sube 4 canciones de prueba (WAV cortos «ZZ Editor ...»), las edita y al final las elimina, junto con
la cuenta «zzedit» y la lista «ZZ Editor». No cambia canciones ni datos de otras cuentas.
--ratelimit: además prueba el límite de ediciones por minuto (deja al admin de prueba bloqueado 1 minuto).
"""

import argparse
import datetime
import http.cookiejar
import io
import json
import os
import re
import secrets
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave

import config  # noqa: F401  (carga .env)

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--admin", default="zzadmin")
ap.add_argument("--admin-pass", default="Prueba-Admin-2026!")
ap.add_argument("--demo-user", default=os.environ.get("SPOTY_DEMO_USER", "demo"))
ap.add_argument("--ratelimit", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")
DEMO_PASS = os.environ.get("SPOTY_DEMO_PASSWORD", "")
USER_PASS = "Clave-de-Editor-2026"
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
        frames = b"".join(struct.pack("<h", (b - 128) * 40) for b in secrets.token_bytes(4000))
        w.writeframes(frames)
    return buf.getvalue()


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a1a8e0a40000000049454e44ae426082"
)


def album(client, album_id):
    return client.api("GET", f"/api/albums/{album_id}")


def track_in(client, album_id, track_id):
    st, data = album(client, album_id)
    return next((t for t in data.get("tracks", []) if t["id"] == track_id), None) if st == 200 else None


def genre_count(client, name):
    st, data = client.api("GET", "/api/genres")
    return next((g["count"] for g in data.get("genres", []) if g["name"] == name), 0)


# ---------- sin sesión ----------
anon = Client()
st, _ = anon.api("PATCH", "/api/tracks/1", {"title": "x"})
check("sin sesión: PATCH /api/tracks/<id> -> 401", st == 401, st)

# ---------- administrador ----------
admin = Client()
st, text = admin.login(args.admin, args.admin_pass)
check("login del administrador de prueba", st in (302, 303), (st, text[:100]))
st, health0 = admin.api("GET", "/api/health")
start_total = health0.get("tracks")

# limpieza de una corrida anterior interrumpida
st, srch = admin.api("GET", "/api/search?q=ZZ%20Editor&all=1")
for t in srch.get("tracks", []):
    if t.get("source") == "user" and t["artist"].lower().startswith("zz editor"):
        admin.api("DELETE", f"/api/tracks/{t['id']}")
st, health0 = admin.api("GET", "/api/health")
start_total = health0.get("tracks")

names = ["ZZ Editor A - Uno.wav", "ZZ Editor A - Dos.wav", "ZZ Editor A - Tres.wav", "ZZ Editor C - Cuatro.wav"]
ids = []
for name in names:
    st, data = admin.multipart("/api/uploads", name, wav_bytes(), "audio/wav")
    check(f"subir pista de prueba {name}", st == 201 and data.get("id"), (st, data))
    ids.append(data.get("id"))
t1, t2, t3, t4 = ids
st, a1 = admin.api("GET", f"/api/uploads?limit=10")
first = {t["id"]: t for t in a1.get("tracks", [])}
album_a = first[t1]["album_id"]
artist_a = first[t1]["artist_id"]
check("las 3 pistas de «ZZ Editor A» quedan en el mismo álbum", first[t2]["album_id"] == album_a == first[t3]["album_id"], [first[i]["album_id"] for i in (t1, t2, t3)])

# datos de usuario que deben conservarse: Me gusta, lista e historial
admin.api("POST", f"/api/likes/{t1}", {"shown_liked": False})
st, pl = admin.api("POST", "/api/playlists", {"name": "ZZ Editor", "description": ""})
playlist_id = pl.get("id")
admin.api("POST", f"/api/playlists/{playlist_id}/tracks", {"track_id": t1})
admin.api("POST", "/api/plays", {"track_id": t1})

# ---------- edición de una canción ----------
year_next = datetime.date.today().year + 1
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"title": "Uno editada", "track_number": 7, "genre": "ZZ Género Prueba", "year": 1999})
tr = res.get("track") or {}
check("admin: editar título, número, género y año -> 200", st == 200 and res.get("ok") is True, (st, res))
check("la respuesta trae la pista con los datos nuevos", tr.get("title") == "Uno editada" and tr.get("track_number") == 7 and tr.get("genre") == "ZZ Género Prueba" and tr.get("year") == 1999, tr)
fresh = track_in(admin, album_a, t1)
check("el álbum muestra la pista editada (base de datos)", fresh and fresh["title"] == "Uno editada" and fresh["track_number"] == 7, fresh)
check("el año es del álbum: lo ven las otras pistas", (track_in(admin, album_a, t2) or {}).get("year") == 1999, track_in(admin, album_a, t2))
check("/api/genres refleja el género nuevo al instante", genre_count(admin, "ZZ Género Prueba") == 1, genre_count(admin, "ZZ Género Prueba"))
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"genre": ""})
check("género vacío -> «Sin género»", st == 200 and res["track"]["genre"] == "Sin género", (st, res.get("track", {}).get("genre")))
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"year": ""})
check("año vacío -> sin año (0)", st == 200 and res["track"]["year"] == 0, (st, res.get("track", {}).get("year")))
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"title": "  Uno \u0007con   espacios  "})
check("el texto se limpia (espacios y caracteres de control)", st == 200 and res["track"]["title"] == "Uno con espacios", res.get("track", {}).get("title"))

# ---------- validaciones ----------
bad = [
    ({"title": ""}, "título vacío"),
    ({"title": "x" * 161}, "título de 161 caracteres"),
    ({"artist": "y" * 121}, "artista de 121 caracteres"),
    ({"album": "   "}, "álbum en blanco"),
    ({"genre": "g" * 81}, "género de 81 caracteres"),
    ({"year": 999}, "año 999"),
    ({"year": year_next + 1}, f"año {year_next + 1}"),
    ({"year": "19x9"}, "año no numérico"),
    ({"year": True}, "año booleano"),
    ({"track_number": -1}, "número de pista negativo"),
    ({"track_number": 10000}, "número de pista 10000"),
    ({"track_number": 1.5}, "número de pista decimal"),
    ({"title": 123}, "título numérico"),
    ({"plays": 100}, "campo no permitido (plays)"),
    ({"audio_file": "../../etc/passwd"}, "campo no permitido (audio_file)"),
    ({}, "sin cambios"),
]
for body, label in bad:
    st, res = admin.api("PATCH", f"/api/tracks/{t1}", body)
    check(f"validación: {label} -> 400", st == 400 and res.get("error"), (st, res))
st, res = admin.api("PATCH", f"/api/tracks/{t1}", None, headers={"Content-Type": "application/json"}, data=b"no-json")
check("validación: cuerpo que no es JSON -> 400", st == 400, st)
st, res = admin.api("PATCH", f"/api/tracks/{t1}", ["title"])
check("validación: cuerpo que no es objeto -> 400", st == 400, st)
st, res = admin.api("PATCH", "/api/tracks/999999999", {"title": "x"})
check("pista inexistente -> 404", st == 404, st)
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"title": "sin csrf"}, csrf=False)
check("sin token CSRF -> 403", st == 403, st)
st, res = admin.api("PATCH", f"/api/tracks/{t1}", {"title": "otro sitio"}, headers={"Origin": "https://evil.example"})
check("Origin de otro sitio -> 403", st == 403, st)
check("tras los rechazos la pista sigue igual", (track_in(admin, album_a, t1) or {}).get("title") == "Uno con espacios", track_in(admin, album_a, t1))
st, srch = admin.api("GET", "/api/search?q=a&all=1")
catalog = next((t for t in srch.get("tracks", []) if t.get("source") == "catalog"), None)
if catalog:
    st, res = admin.api("PATCH", f"/api/tracks/{catalog['id']}", {"title": "x"})
    check("pista del catálogo editorial (no subida) -> 403", st == 403, st)

# ---------- mover una pista a otro álbum ----------
st, res = admin.api("PATCH", f"/api/tracks/{t2}", {"album": "ZZ Álbum Nuevo"})
album_new = (res.get("track") or {}).get("album_id")
check("mover una pista a un álbum nuevo -> se crea el álbum", st == 200 and album_new and album_new != album_a and res["summary"]["albums_created"] == 1, (st, res.get("summary")))
check("el álbum anterior sigue con las otras pistas", album(admin, album_a)[0] == 200, album(admin, album_a)[0])

# ---------- lote: juntar en el álbum existente y limpiar el vacío ----------
st, res = admin.api("PATCH", "/api/tracks/batch", {"ids": [t1, t3], "changes": {"album": "ZZ Álbum Nuevo"}})
check("lote: 2 pistas al álbum existente -> 200", st == 200 and res.get("summary", {}).get("updated") == 2, (st, res))
check("lote: reutiliza el álbum (no crea otro)", all(t["album_id"] == album_new for t in res.get("tracks", [])) and res["summary"]["albums_created"] == 0, res.get("summary"))
check("lote: el álbum que quedó vacío se borra (huérfano)", album(admin, album_a)[0] == 404 and res["summary"]["albums_removed"] == 1, (album(admin, album_a)[0], res.get("summary")))
st, data = album(admin, album_new)
check("el álbum nuevo tiene las 3 pistas", st == 200 and sorted(t["id"] for t in data["tracks"]) == sorted([t1, t2, t3]), [t["id"] for t in data.get("tracks", [])])

# ---------- lote: cambiar artista (el álbum lo sigue y se renombra en su lugar) ----------
st, res = admin.api("PATCH", "/api/tracks/batch", {"ids": [t1, t2, t3], "changes": {"artist": "ZZ Editor B"}})
artist_b = res.get("tracks", [{}])[0].get("artist_id")
check("lote: cambiar artista de 3 pistas -> 200", st == 200 and all(t["artist"] == "ZZ Editor B" for t in res.get("tracks", [])), (st, res))
check("lote: el álbum completo conserva su id y pasa al artista nuevo", all(t["album_id"] == album_new for t in res["tracks"]) and album(admin, album_new)[1].get("artist") == "ZZ Editor B", album(admin, album_new)[1].get("artist"))
check("lote: el artista anterior sin canciones se borra (huérfano)", admin.api("GET", f"/api/artists/{artist_a}")[0] == 404 and res["summary"]["artists_removed"] >= 1, res.get("summary"))

# ---------- lote: género común ----------
st, res = admin.api("PATCH", "/api/tracks/batch", {"ids": [t1, t2, t3, t4], "changes": {"genre": "ZZ Género Lote", "year": 2001}})
check("lote: género y año a 4 pistas -> 200", st == 200 and all(t["genre"] == "ZZ Género Lote" and t["year"] == 2001 for t in res.get("tracks", [])), (st, res.get("tracks")))
check("lote: /api/genres cuenta las 4", genre_count(admin, "ZZ Género Lote") == 4, genre_count(admin, "ZZ Género Lote"))
st, gd = admin.api("GET", "/api/genres")
slug = next((g["slug"] for g in gd.get("genres", []) if g["name"] == "ZZ Género Lote"), "")
st, gdet = admin.api("GET", f"/api/genres/{slug}")
check("lote: la página del género lista las 4 pistas", st == 200 and sorted(t["id"] for t in gdet.get("tracks", [])) == sorted(ids), (st, len(gdet.get("tracks", []))))

# ---------- corregir mayúsculas de un artista (mismo artista, sin duplicar) ----------
st, res = admin.api("PATCH", f"/api/tracks/{t4}", {"artist": "ZZ Editor B"})
check("pista de otro artista pasa a un artista existente (lo reutiliza)", st == 200 and res["track"]["artist_id"] == artist_b, (st, res.get("track", {}).get("artist_id"), artist_b))
st, res = admin.api("PATCH", f"/api/tracks/{t4}", {"artist": "zz editor b"})
check("mismas letras con otras mayúsculas: corrige el nombre sin crear otro artista", st == 200 and res["track"]["artist_id"] == artist_b and res["track"]["artist"] == "zz editor b", res.get("track"))
admin.api("PATCH", f"/api/tracks/{t4}", {"artist": "ZZ Editor B"})

# ---------- validaciones del lote ----------
bad_batch = [
    ({"ids": [t1], "changes": {"title": "x"}}, "título en lote"),
    ({"ids": [t1], "changes": {"track_number": 1}}, "número de pista en lote"),
    ({"ids": [], "changes": {"genre": "x"}}, "sin canciones"),
    ({"ids": ["1"], "changes": {"genre": "x"}}, "id de texto"),
    ({"ids": [t1], "changes": {}}, "sin cambios"),
    ({"ids": list(range(1, 502)), "changes": {"genre": "x"}}, "más de 500 canciones"),
    ({"changes": {"genre": "x"}}, "sin lista de ids"),
]
for body, label in bad_batch:
    st, res = admin.api("PATCH", "/api/tracks/batch", body)
    check(f"lote: {label} -> 400", st == 400, (st, res))
st, res = admin.api("PATCH", "/api/tracks/batch", {"ids": [t1, 999999999], "changes": {"genre": "ZZ Nunca"}})
check("lote con una pista inexistente -> 404 y no cambia ninguna", st == 404 and genre_count(admin, "ZZ Nunca") == 0, (st, genre_count(admin, "ZZ Nunca")))

# ---------- portada del álbum ----------
st, res = admin.multipart(f"/api/albums/{album_new}/cover", "portada.png", PNG_1PX, "image/png")
check("admin: cambiar portada del álbum (PNG) -> 200", st == 200 and re.match(r"^/static/covers/user/[0-9a-f]{16}\.png$", res.get("cover_url", "")), (st, res))
st, data = album(admin, album_new)
check("el álbum usa la portada nueva", data.get("cover_url") == res.get("cover_url"), data.get("cover_url"))
st, h, body = admin.raw("GET", res.get("cover_url", "/x"))
check("la portada nueva se puede ver", st == 200 and body.startswith(b"\x89PNG"), st)
st, res = admin.multipart(f"/api/albums/{album_new}/cover", "x.gif", b"GIF89a" + b"\x00" * 20, "image/gif")
check("portada GIF -> 415", st == 415, st)
st, res = admin.multipart(f"/api/albums/{album_new}/cover", "x.png", b"<svg onload=alert(1)>", "image/png")
check("portada que no es imagen (aunque diga .png) -> 415", st == 415, st)
st, res = admin.multipart("/api/albums/999999999/cover", "p.png", PNG_1PX, "image/png")
check("portada de álbum inexistente -> 404", st == 404, st)

# ---------- Me gusta, listas e historial se conservan ----------
st, likes = admin.api("GET", "/api/likes")
check("Me gusta conservado tras las ediciones", any(t["id"] == t1 for t in likes), [t["id"] for t in likes][:5])
st, pld = admin.api("GET", f"/api/playlists/{playlist_id}")
check("la lista conserva la pista editada", st == 200 and any(t["id"] == t1 and t["title"] == "Uno con espacios" for t in pld.get("tracks", [])), (st, [t.get("title") for t in pld.get("tracks", [])]))
st, home = admin.api("GET", "/api/home")
check("el historial conserva la pista editada", any(t["id"] == t1 for t in home.get("recent", [])), [t["id"] for t in home.get("recent", [])][:5])
st, h, raw = admin.raw("GET", (track_in(admin, album_new, t1) or {}).get("audio_url", "/x"), headers={"Range": "bytes=0-99"})
check("el audio sigue sonando (206) tras editar", st in (200, 206) and raw[:4] == b"RIFF", st)

# ---------- cuenta normal ----------
st, data = admin.api("GET", "/api/admin/users")
for x in data.get("users", []):
    if x["username"] == "zzedit":
        admin.api("DELETE", f"/api/admin/users/{x['id']}")
u = Client()
u.form("/register", {"username": "zzedit", "display_name": "Editor Prueba", "password": USER_PASS, "confirm": USER_PASS})
st, data = admin.api("GET", "/api/admin/users")
zid = next((x["id"] for x in data.get("users", []) if x["username"] == "zzedit"), None)
admin.api("POST", f"/api/admin/users/{zid}/approve")
st, text = u.login("zzedit", USER_PASS)
check("login de la cuenta normal", st in (302, 303), st)
st, _ = u.api("PATCH", f"/api/tracks/{t1}", {"title": "hackeada"})
check("normal: editar una canción -> 403", st == 403, st)
st, _ = u.api("PATCH", "/api/tracks/batch", {"ids": [t1], "changes": {"genre": "hack"}})
check("normal: edición por lote -> 403", st == 403, st)
st, _ = u.multipart(f"/api/albums/{album_new}/cover", "p.png", PNG_1PX, "image/png")
check("normal: cambiar portada -> 403", st == 403, st)

# ---------- cuenta demo ----------
if DEMO_PASS:
    d = Client()
    st, text = d.login(args.demo_user, DEMO_PASS)
    check("login de la demo", st in (302, 303), st)
    st, _ = d.api("PATCH", f"/api/tracks/{t1}", {"title": "demo"})
    check("demo: editar una canción -> 403", st == 403, st)
    st, _ = d.api("PATCH", "/api/tracks/batch", {"ids": [t1], "changes": {"genre": "demo"}})
    check("demo: edición por lote -> 403", st == 403, st)
    st, _ = d.multipart(f"/api/albums/{album_new}/cover", "p.png", PNG_1PX, "image/png")
    check("demo: cambiar portada -> 403", st == 403, st)
else:
    print("(sin SPOTY_DEMO_PASSWORD: se omiten las pruebas de la demo)")
check("los rechazos no cambiaron la pista", (track_in(admin, album_new, t1) or {}).get("title") == "Uno con espacios" and genre_count(admin, "hack") == 0 and genre_count(admin, "demo") == 0, track_in(admin, album_new, t1))

# ---------- interfaz ----------
st, h, body = admin.raw("GET", "/")
m = re.search(r'/static/js/main\.js\?v=([0-9a-z]+)', body.decode("utf-8", "replace"))
st, h, body = admin.raw("GET", f"/static/js/ui.js?v={m.group(1) if m else 'x'}")
js = body.decode("utf-8", "replace")
check("ui.js: «Editar canción» y «Seleccionar varias» solo para quien puede editar (admin o editor)", 'track.source === "user" && canEdit() ? `${item("edit-track"' in js and "select-many" in js, "")
check("ui.js: barra de selección y edición por lote", "select-bar" in js and "editSelectedForm" in js and "editTracks" in js, "")
st, h, body = admin.raw("GET", f"/static/js/api.js?v={m.group(1) if m else 'x'}")
check("api.js: editTrack, editTracks y albumCover", all(k in body for k in (b"editTrack:", b"editTracks:", b"albumCover:")), "")

# ---------- límite de ediciones ----------
if args.ratelimit:
    codes = [admin.api("PATCH", f"/api/tracks/{t1}", {"title": "Uno con espacios"})[0] for _ in range(65)]
    check("límite: tras 60 ediciones en un minuto -> 429", 429 in codes and codes.index(429) <= 60, codes[-6:])

# ---------- limpieza ----------
admin.api("POST", f"/api/likes/{t1}", {"shown_liked": True})
st, _ = admin.api("DELETE", f"/api/playlists/{playlist_id}")
check("borrar la lista de prueba", st == 200, st)
if args.ratelimit:
    import time
    time.sleep(61)
for track_id in ids:
    st, _ = admin.api("DELETE", f"/api/tracks/{track_id}")
    check(f"borrar pista de prueba {track_id}", st == 200, st)
st, health1 = admin.api("GET", "/api/health")
check("la biblioteca vuelve a la cantidad inicial", health1.get("tracks") == start_total, (start_total, health1.get("tracks")))
st, srch = admin.api("GET", "/api/search?q=ZZ%20Editor&all=1")
check("no quedan artistas ni álbumes de prueba (huérfanos)", not srch.get("artists") and not srch.get("albums") and not srch.get("tracks"), (len(srch.get("artists", [])), len(srch.get("albums", []))))
check("no quedan géneros de prueba", genre_count(admin, "ZZ Género Lote") == 0 and genre_count(admin, "ZZ Género Prueba") == 0, "")
st, data = admin.api("GET", "/api/admin/users")
for x in data.get("users", []):
    if x["username"] == "zzedit":
        st, res = admin.api("DELETE", f"/api/admin/users/{x['id']}")
        check("borrar la cuenta de prueba", st == 200, (st, res))

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
