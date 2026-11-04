# -*- coding: utf-8 -*-
"""Pruebas de Mis canciones / offline / lite (solo copia test_*).

Uso:
  python tests_offline.py --db test_spotyofo_offline [--port 5101]

- Crea zzoff1, prueba likes, /api/me.offline_downloads, /api/audio/lite y Range.
- Demo: likes permitidos, offline_downloads=false.
- No toca produccion ni la base spotyofo.
"""
from __future__ import annotations

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
ap.add_argument("--port", type=int, default=5101)
ap.add_argument("--demo-user", default=None)
args = ap.parse_args()
if not re.fullmatch(r"test_[a-z0-9_]+", args.db):
    sys.exit("La base de pruebas debe llamarse test_<algo> (nunca la base real).")
os.environ["SPOTY_DB_NAME"] = args.db

import accounts  # noqa: E402
import audiolite  # noqa: E402
import config  # noqa: E402,F401
import db as dbmod  # noqa: E402

DEMO_USER = args.demo_user or os.environ.get("SPOTY_DEMO_USER", "demo")
DEMO_PASS = os.environ.get("SPOTY_DEMO_PASSWORD", "")
if not DEMO_PASS:
    # load from .env without printing
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        for ln in open(env_path, encoding="utf-8"):
            if ln.startswith("SPOTY_DEMO_PASSWORD="):
                DEMO_PASS = ln.split("=", 1)[1].strip().strip('"').strip("'")
                os.environ["SPOTY_DEMO_PASSWORD"] = DEMO_PASS
                break
if not DEMO_PASS:
    sys.exit("Falta SPOTY_DEMO_PASSWORD.")

BASE = f"http://127.0.0.1:{args.port}"
PASS = "Offline-Prueba-2026!"
HERE = os.path.dirname(os.path.abspath(__file__))
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:400]}"))


conn = dbmod.connect()
with conn.cursor() as cur:
    cur.execute("SELECT DATABASE() AS d")
    assert cur.fetchone()["d"] == args.db
dbmod.init_schema(conn)


def cleanup():
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = 'zzoff1'")
        row = cur.fetchone()
        if row:
            uid = row["id"]
            cur.execute("DELETE FROM user_likes WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))


cleanup()
accounts.create_user(conn, "zzoff1", "ZZOFF1", PASS, role="user", status="active")
with conn.cursor() as cur:
    cur.execute("SELECT id, audio_file FROM tracks WHERE audio_file IS NOT NULL AND audio_file != '' ORDER BY id LIMIT 5")
    TRACKS = cur.fetchall()
assert TRACKS, "La copia no tiene pistas"

# ensure at least one lite exists for first track if source file present
LITE_INFO = None
src = audiolite.source_path(TRACKS[0]["audio_file"])
if src.exists():
    try:
        LITE_INFO = audiolite.ensure_lite(TRACKS[0]["audio_file"])
    except Exception as exc:  # noqa: BLE001
        LITE_INFO = {"error": str(exc)}

log_path = os.path.join(HERE, "instance", "tests_offline_app.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
env = dict(
    os.environ,
    SPOTY_DB_NAME=args.db,
    SPOTY_PORT=str(args.port),
    SPOTY_HOST="127.0.0.1",
    SPOTY_DEMO_ENABLED="1",
    PYTHONUNBUFFERED="1",
)
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

    def api(self, method, path, body=None):
        headers = {"X-Requested-With": "SpotyOFO", "Accept": "application/json", "X-CSRF-Token": self.csrf}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        st_, h, raw = self.raw(method, path, data, headers)
        if h.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        try:
            return st_, json.loads(raw or b"{}")
        except ValueError:
            return st_, {"_raw": raw[:200]}

    def login(self, user, password):
        st, h, text = self.form("/login", {"username": user, "password": password})
        self.after_login()
        return st, h, text

    def after_login(self):
        _, me = self.api("GET", "/api/me")
        self.csrf = me.get("csrf", "")
        return me


try:
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    else:
        raise SystemExit("La app de prueba no arranco; ver " + log_path)

    # --- anon ---
    anon = Client()
    s, d = anon.api("GET", "/api/likes")
    check("anon /api/likes -> 401", s == 401, (s, d))
    s, d = anon.api("GET", "/api/audio/lite?ids=1")
    check("anon /api/audio/lite -> 401", s == 401, (s, d))
    s, d = anon.api("GET", "/api/me")
    check("anon /api/me -> 401", s == 401, (s, d))

    # --- usuario normal ---
    u = Client()
    st, h, _ = u.login("zzoff1", PASS)
    check("login zzoff1", st in (302, 303), st)
    me = u.after_login()
    check("zzoff1 offline_downloads true", me.get("offline_downloads") is True, me)
    check("zzoff1 no es demo", me.get("demo") is not True, me)

    tid = TRACKS[0]["id"]
    s, d = u.api("POST", f"/api/likes/{tid}", {"shown_liked": False})
    check("like track", s == 200 and d.get("liked") is True, (s, d))
    s, likes = u.api("GET", "/api/likes")
    ids = [t["id"] for t in likes] if isinstance(likes, list) else []
    check("likes lista incluye pista", tid in ids, ids[:10])
    # lite fields on liked track
    liked = next((t for t in likes if t["id"] == tid), {})
    if LITE_INFO and LITE_INFO.get("url"):
        check("like serializa lite_url", bool(liked.get("lite_url")), liked)
        check("like serializa lite_size", isinstance(liked.get("lite_size"), int) and liked["lite_size"] > 0, liked)
    else:
        check("like sin lite (archivo ausente o fallo encode) sigue ok", "id" in liked, liked)

    ids_q = ",".join(str(t["id"]) for t in TRACKS[:3])
    s, d = u.api("GET", f"/api/audio/lite?ids={ids_q}")
    check("audio/lite 200", s == 200 and "items" in d, (s, d))
    items = d.get("items") or []
    check("audio/lite items len", len(items) == min(3, len(TRACKS)), len(items))
    if LITE_INFO and LITE_INFO.get("url"):
        first = next((x for x in items if x.get("id") == tid), {})
        check("audio/lite marca ready", first.get("ready") is True, first)

    # Range online still 206 on original audio URL from track serialize
    s, tracks = u.api("GET", f"/api/tracks/{tid}")
    # may be single object or not exist — try library
    if s != 200 or not isinstance(tracks, dict) or not tracks.get("audio_url"):
        # get from likes again
        audio_url = liked.get("audio_url") or liked.get("url")
    else:
        audio_url = tracks.get("audio_url")
    if not audio_url:
        # fallback path
        audio_url = f"/media/{TRACKS[0]['audio_file']}"
        s2, h2, body2 = u.raw("GET", audio_url, None, {"Range": "bytes=0-1", "X-Requested-With": "SpotyOFO"})
        if s2 == 404:
            audio_url = f"/static/audio/{TRACKS[0]['audio_file']}"
    s2, h2, body2 = u.raw("GET", audio_url, None, {"Range": "bytes=0-1", "X-Requested-With": "SpotyOFO"})
    check("Range original -> 206", s2 == 206, (s2, audio_url, h2.get("Content-Range")))
    if LITE_INFO and LITE_INFO.get("url"):
        s3, h3, body3 = u.raw("GET", LITE_INFO["url"], None, {"Range": "bytes=0-1", "X-Requested-With": "SpotyOFO"})
        check("Range lite -> 206", s3 == 206, (s3, LITE_INFO["url"], h3.get("Content-Range")))

    s, d = u.api("POST", f"/api/likes/{tid}", {"shown_liked": True})
    check("unlike track", s == 200 and d.get("liked") is False, (s, d))

    # --- demo: likes ok, offline flag false ---
    demo = Client()
    st, h, _ = demo.login(DEMO_USER, DEMO_PASS)
    check("login demo", st in (302, 303), st)
    me = demo.after_login()
    check("demo offline_downloads false", me.get("offline_downloads") is False, me)
    check("demo flag", me.get("demo") is True, me)
    s, likes = demo.api("GET", "/api/likes")
    check("demo puede listar likes", s == 200 and isinstance(likes, list), (s, type(likes)))
    # Elegir una pista que la demo NO tenga en Me gusta, y otra que SI tenga.
    liked_ids = {t["id"] for t in likes}
    tid_add = next((t["id"] for t in TRACKS if t["id"] not in liked_ids), None)
    tid_del = next(iter(liked_ids), None)
    if tid_add is not None:
        s, d = demo.api("POST", f"/api/likes/{tid_add}", {"shown_liked": False})
        check("demo puede dar me gusta", s == 200 and d.get("liked") is True, (s, d))
        s, d = demo.api("POST", f"/api/likes/{tid_add}", {"shown_liked": True})
        check("demo puede quitar me gusta (recien agregada)", s == 200 and d.get("liked") is False, (s, d))
    else:
        check("demo puede dar me gusta", False, "no hay pista sin like")
        check("demo puede quitar me gusta (recien agregada)", False, "skip")
    if tid_del is not None:
        s, d = demo.api("POST", f"/api/likes/{tid_del}", {"shown_liked": True})
        check("demo puede quitar me gusta (semilla)", s == 200 and d.get("liked") is False, (s, d))
        s, d = demo.api("POST", f"/api/likes/{tid_del}", {"shown_liked": False})
        check("demo puede restaurar me gusta (semilla)", s == 200 and d.get("liked") is True, (s, d))
    else:
        check("demo puede quitar me gusta (semilla)", False, "demo sin likes semilla")
        check("demo puede restaurar me gusta (semilla)", False, "skip")

    # unit: attach_lite_fields idempotent
    row = {"id": tid, "audio_file": TRACKS[0]["audio_file"], "title": "x"}
    out = audiolite.attach_lite_fields(dict(row))
    check("attach_lite_fields keys", "lite_url" in out and "lite_size" in out and "lite_codec" in out, out)
    if LITE_INFO and LITE_INFO.get("url"):
        check("attach finds lite", out.get("lite_url") == LITE_INFO.get("url"), out)

finally:
    app_proc.terminate()
    try:
        app_proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        app_proc.kill()
    logf.close()
    cleanup()
    conn.close()

print(f"\n{count} checks, {'OK' if ok else 'FALLÓ'}")
sys.exit(0 if ok else 1)
