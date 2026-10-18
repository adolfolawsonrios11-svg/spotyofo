"""Pruebas de la reproducción automática sin fin (/api/tracks/<id>/related).

Uso (contra una COPIA de la base; el nombre debe empezar con «test_»):
  python tests_related.py --db test_spotyofo_x [--port 5099]

- Agrega a la copia un mini catálogo «zzrel-*» (3 artistas, 4 géneros) para comprobar el orden por
  niveles: mismo género -> géneros vecinos (comparten artista) -> resto del catálogo. Lo borra al final.
- Crea la cuenta zzrel1 y la borra al final. Usa la cuenta demo de la copia con el botón «Probar demo».
"""

import argparse
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--port", type=int, default=5099)
args = ap.parse_args()
if not re.fullmatch(r"test_[a-z0-9_]+", args.db):
    sys.exit("La base de pruebas debe llamarse test_<algo> (nunca la base real).")
os.environ["SPOTY_DB_NAME"] = args.db  # antes de importar config: .env no lo pisa

import accounts  # noqa: E402
import config  # noqa: E402,F401
import db as dbmod  # noqa: E402

BASE = f"http://127.0.0.1:{args.port}"
PASS = "Relacionadas-2026!"
HERE = os.path.dirname(os.path.abspath(__file__))
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:400]}"))


# ---------------------------------------------------------------- datos en la copia
conn = dbmod.connect()
with conn.cursor() as cur:
    cur.execute("SELECT DATABASE() AS d")
    assert cur.fetchone()["d"] == args.db
dbmod.init_schema(conn)


def cleanup():
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = 'zzrel1'")
        cur.execute("SELECT id FROM tracks WHERE slug LIKE 'zzrel-%%'")
        tids = [r["id"] for r in cur.fetchall()]
        if tids:
            marks = ",".join(["%s"] * len(tids))
            for table in ("play_history", "user_likes", "playlist_tracks"):
                cur.execute(f"DELETE FROM {table} WHERE track_id IN ({marks})", tids)
            cur.execute(f"DELETE FROM tracks WHERE id IN ({marks})", tids)
        cur.execute("DELETE FROM albums WHERE slug LIKE 'zzrel-%%'")
        cur.execute("DELETE FROM artists WHERE slug LIKE 'zzrel-%%'")


cleanup()
FIX = {}  # nombre -> id
with conn.cursor() as cur:
    for key in ("a", "b", "c"):
        cur.execute(
            "INSERT INTO artists (slug, name, genre, origin, bio, accent_a, accent_b, cover_file) "
            "VALUES (%s, %s, 'ZZ', '', '', '#111111', '#222222', 'pending.svg')", (f"zzrel-{key}", f"ZZ Artista {key.upper()}"))
        artist_id = cur.lastrowid
        cur.execute(
            "INSERT INTO albums (slug, artist_id, title, year, credit, accent_a, accent_b, cover_file) "
            "VALUES (%s, %s, %s, 2026, '', '#111111', '#222222', 'pending.svg')", (f"zzrel-{key}", artist_id, f"ZZ Disco {key.upper()}"))
        FIX["artist_" + key] = (artist_id, cur.lastrowid)
    plan = [("u1", "a", "ZZ Uno"), ("u2", "a", "ZZ Uno"), ("u3", "a", "ZZ Uno"),
            ("d1", "a", "ZZ Dos"), ("d2", "b", "ZZ Dos"), ("d3", "b", "ZZ Dos"),
            ("t1", "c", "ZZ Tres"), ("t2", "c", "ZZ Tres"),
            ("s1", "c", "ZZ Solo")]
    for n, (name, key, genre) in enumerate(plan, 1):
        artist_id, album_id = FIX["artist_" + key]
        cur.execute(
            "INSERT INTO tracks (slug, album_id, artist_id, title, track_number, duration_ms, audio_file, genre, bpm, musical_key) "
            "VALUES (%s, %s, %s, %s, %s, 180000, %s, %s, 100, 'C')",
            (f"zzrel-{name}", album_id, artist_id, f"ZZ {name}", n, f"zzrel/{name}.mp3", genre))
        FIX[name] = cur.lastrowid
    cur.execute("SELECT COUNT(*) AS n FROM tracks")
    TOTAL = cur.fetchone()["n"]
    cur.execute("SELECT id FROM users WHERE username = 'zzrel1'")
accounts.create_user(conn, "zzrel1", "ZZREL1", PASS, role="user", status="active")
GENRE = {FIX[n]: g for n, _, g in plan}
UNO = {FIX[n] for n in ("u1", "u2", "u3")}
DOS = {FIX[n] for n in ("d1", "d2", "d3")}
TRES = {FIX[n] for n in ("t1", "t2")}

# ---------------------------------------------------------------- app de prueba
log_path = os.path.join(HERE, "instance", "tests_related_app.log")
env = dict(os.environ, SPOTY_DB_NAME=args.db, SPOTY_PORT=str(args.port), SPOTY_HOST="127.0.0.1",
           SPOTY_DEMO_ENABLED="1", PYTHONUNBUFFERED="1")
logf = open(log_path, "w", encoding="utf-8")
app_proc = subprocess.Popen([sys.executable, "app.py"], cwd=HERE, env=env, stdout=logf, stderr=subprocess.STDOUT)


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
            with self.opener.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def form(self, path, fields, page=None):
        _, _, body = self.raw("GET", page or path)
        m = re.search(r'name="csrf_token" value="([^"]+)"', body.decode("utf-8", "replace"))
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}
        data = urllib.parse.urlencode({"csrf_token": m.group(1) if m else "", **fields}).encode()
        return self.raw("POST", path, data, headers)

    def api(self, method, path):
        st_, h, raw = self.raw(method, path, None, {"X-Requested-With": "SpotyOFO", "Accept": "application/json", "X-CSRF-Token": self.csrf})
        if h.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        try:
            return st_, json.loads(raw or b"{}")
        except ValueError:
            return st_, {"_raw": raw[:200]}

    def after_login(self):
        _, me = self.api("GET", "/api/me")
        self.csrf = me.get("csrf", "")
        return me


def rel(c, track_id, **params):
    query = urllib.parse.urlencode(params)
    return c.api("GET", f"/api/tracks/{track_id}/related" + (f"?{query}" if query else ""))


def ids_of(items):
    return [item["id"] for item in items] if isinstance(items, list) else []


try:
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    else:
        raise SystemExit("La app de prueba no arrancó; ver " + log_path)

    # ---------- sesión obligatoria ----------
    anon = Client()
    s, d = rel(anon, FIX["u1"])
    check("sin sesión /related -> 401 (needs=login)", s == 401 and d.get("needs") == "login", (s, d))
    s, d = rel(anon, FIX["u1"], exclude="1,2", seed=5)
    check("sin sesión con parámetros -> 401", s == 401, (s, d))

    a = Client()
    st_, _, _ = a.form("/login", {"username": "zzrel1", "password": PASS})
    check("login zzrel1", st_ in (302, 303), st_)
    a.after_login()

    # ---------- forma y tamaño ----------
    s, items = rel(a, FIX["u1"], seed=7)
    got = ids_of(items)
    check("200 y lista de canciones", s == 200 and isinstance(items, list), (s, items))
    check("devuelve 20 por defecto (o todo el catálogo menos la actual)", len(got) == min(20, TOTAL - 1), (len(got), TOTAL))
    check("sin duplicados", len(got) == len(set(got)), got)
    check("nunca incluye la canción actual", FIX["u1"] not in got, got)
    check("cada canción trae audio_url y artista", all(i.get("audio_url", "").startswith("/static/audio/") and i.get("artist") for i in items), items[:2])

    # ---------- orden por niveles ----------
    check("nivel 1: primero el mismo género (las otras 2 de ZZ Uno)", set(got[:2]) == UNO - {FIX["u1"]}, got[:4])
    check("nivel 2: después géneros vecinos (ZZ Dos comparte artista A)", set(got[2:5]) == DOS, got[:6])
    check("nivel 3: completa con el resto del catálogo", len(got) > 5 and all(GENRE.get(i) not in ("ZZ Uno", "ZZ Dos") for i in got[5:]), got[5:10])
    s, items = rel(a, FIX["s1"], seed=7, limit=50)
    got = ids_of(items)
    check("género de una sola canción: rellena con vecinos (ZZ Tres, mismo artista) y catálogo",
          s == 200 and set(got[:2]) == TRES and len(got) == min(50, TOTAL - 1), got[:4])
    vecinos_d2 = ids_of(rel(a, FIX["d2"], seed=3, limit=50)[1])
    check("vecinos desde ZZ Dos: después de su género vienen ZZ Uno (artista A en común)",
          set(vecinos_d2[:2]) == DOS - {FIX["d2"]} and set(vecinos_d2[2:5]) == UNO, vecinos_d2[:6])

    # ---------- exclude ----------
    s, items = rel(a, FIX["u1"], exclude=f"{FIX['u2']}", seed=7)
    got = ids_of(items)
    check("exclude saca esa canción", s == 200 and FIX["u2"] not in got and got[0] == FIX["u3"], got[:3])
    excl = sorted(UNO | DOS)
    s, items = rel(a, FIX["u1"], exclude=",".join(map(str, excl)), seed=7, limit=50)
    got = ids_of(items)
    check("excluyendo ZZ Uno y ZZ Dos solo queda el resto del catálogo", s == 200 and not (set(got) & set(excl)) and len(got) == min(50, TOTAL - 1 - (len(excl) - 1)), (len(got), got[:5]))
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tracks ORDER BY id")
        all_ids = [r["id"] for r in cur.fetchall()]
    others = [i for i in all_ids if i != FIX["u1"]]
    s, items = rel(a, FIX["u1"], exclude=",".join(map(str, others)))
    check("si ya está todo excluido devuelve lista vacía (no error)", s == 200 and items == [], (s, items))
    big = list(range(900000, 900400)) + [FIX["u2"]]
    s, items = rel(a, FIX["u1"], exclude=",".join(map(str, big)), seed=7)
    check("exclude con más de 300 ids: se usa el tope (los últimos), sin error", s == 200 and FIX["u2"] not in ids_of(items), (s, ids_of(items)[:3]))
    s, items = rel(a, FIX["u1"], exclude=",".join(["1"] * 1300))
    check("exclude desmedido -> 400", s == 400, s)
    for bad in ("abc", "1,-2", "1;2", "12345678901"):
        s, d = rel(a, FIX["u1"], exclude=bad)
        check(f"exclude inválido «{bad}» -> 400", s == 400, (s, d))
    s, items = rel(a, FIX["u1"], exclude=" , ,", seed=1)
    check("exclude vacío se ignora", s == 200 and len(ids_of(items)) == min(20, TOTAL - 1), s)

    # ---------- semilla y límite ----------
    one = ids_of(rel(a, FIX["u1"], seed=12345, limit=50)[1])
    two = ids_of(rel(a, FIX["u1"], seed=12345, limit=50)[1])
    other = [ids_of(rel(a, FIX["u1"], seed=k, limit=50)[1]) for k in (1, 2, 3)]
    check("misma semilla -> mismo orden", one == two and one, (one[:5], two[:5]))
    check("otra semilla -> otro orden (variedad)", any(o[5:] != one[5:] for o in other), [o[5:9] for o in other])
    check("sin semilla también responde", rel(a, FIX["u1"])[0] == 200)
    check("limit=5 -> 5", len(ids_of(rel(a, FIX["u1"], limit=5, seed=1)[1])) == 5)
    check("limit se limita a 50", len(ids_of(rel(a, FIX["u1"], limit=999, seed=1)[1])) == min(50, TOTAL - 1))
    for bad in ({"limit": "x"}, {"seed": "x"}):
        check(f"parámetro inválido {bad} -> 400", rel(a, FIX["u1"], **bad)[0] == 400)
    check("canción inexistente -> 404", rel(a, 999999999)[0] == 404)

    # ---------- demo ----------
    demo = Client()
    st_, h, _ = demo.form("/login/demo", {}, page="/login")
    check("entrar con «Probar demo»", st_ in (302, 303), st_)
    me = demo.after_login()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM play_history")
        before = cur.fetchone()["n"]
    s, items = rel(demo, FIX["u1"], exclude=str(FIX["u2"]), seed=7)
    check("la demo puede pedir relacionadas (solo lectura)", s == 200 and FIX["u2"] not in ids_of(items) and len(ids_of(items)) == min(20, TOTAL - 2), (s, me.get("user", {}).get("is_demo")))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM play_history")
        check("pedir relacionadas no escribe nada", cur.fetchone()["n"] == before)
    s, d = demo.api("POST", f"/api/tracks/{FIX['u1']}/related")
    check("POST /related no existe (405)", s == 405, s)

    # ---------- cliente ----------
    s, h, body = a.raw("GET", "/")
    html = body.decode("utf-8", "replace")
    m = re.search(r'/static/js/main\.js\?v=([\w.-]+)', html)
    _, _, mj = a.raw("GET", f"/static/js/main.js?v={m.group(1) if m else ''}")
    check("main.js importa la versión nueva de los módulos", b"?v=20261018a" in mj and b"?v=20261017a" not in mj, mj[:120])
    _, _, pj = a.raw("GET", "/static/js/player.js")
    pj = pj.decode("utf-8", "replace")
    check("player.js: sin fin, reintentos 1-3-10 s y aviso de fin", "pickRelated" in pj and "[1000, 3000, 10000]" in pj and "Se terminó la lista." in pj and "RELATED_NO_REPEAT = [20, 5, 1]" in pj, "")
    _, _, aj = a.raw("GET", "/static/js/api.js")
    check("api.js manda exclude y seed", b'params.set("exclude"' in aj and b'params.set("seed"' in aj, "")
finally:
    app_proc.terminate()
    try:
        app_proc.wait(10)
    except subprocess.TimeoutExpired:
        app_proc.kill()
    logf.close()
    cleanup()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE slug LIKE 'zzrel-%%'")
        left = cur.fetchone()["n"]
    conn.close()

check("se borró el mini catálogo de prueba", left == 0, left)
log_text = open(log_path, encoding="utf-8", errors="replace").read()
check("el log de la app no tiene errores no controlados", "Traceback" not in log_text and "Error no controlado" not in log_text, log_text[-600:])
print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
