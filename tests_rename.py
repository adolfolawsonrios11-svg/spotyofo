"""Pruebas del cambio de nombre a OFO Music y del doble host (spotyofo.ofotech.cl + music.ofotech.cl).

Uso (app local corriendo en BASE con la base de PRUEBA):
  python tests_rename.py http://127.0.0.1:5050 [--port2 5107]

- Texto visible: «OFO Music» en login, índice, manifest, título y página de compartir; sin «SpotyOFO» visible.
- Identificadores internos intactos (cookie, claves de localStorage, cachés, cabecera X-Requested-With, package id).
- Doble host: se simula con la cabecera Host (la app no tiene el host fijo): login, cookies sin Domain, CSRF/Origin por host,
  CSP sin hosts, sin CORS, assetlinks y /s/<token> en ambos.
- Redirección 301: levanta una segunda instancia (puerto --port2) con SPOTY_CANONICAL_HOST/SPOTY_LEGACY_HOSTS/SPOTY_LEGACY_REDIRECT=1
  y comprueba qué se redirige y qué no. Con la instancia principal (sin esas variables) no se redirige nada.
Crea la cuenta zzrn_user y la borra al final. Solo biblioteca estándar + los módulos del proyecto.
"""

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config  # noqa: F401  (carga .env)
import accounts
import db

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--port2", type=int, default=5107)
args = ap.parse_args()
BASE = args.base.rstrip("/")
ROOT = Path(__file__).resolve().parent
NEW, OLD = "music.ofotech.cl", "spotyofo.ofotech.cl"
PASS = "Clave-Renombre-2026!"
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:300]}"))


class Conn:
    """Cliente mínimo con cookies que permite fijar la cabecera Host."""

    def __init__(self, base, host=None):
        u = urllib.parse.urlparse(base)
        self.addr, self.port, self.host = u.hostname, u.port or 80, host or u.netloc
        self.cookies = {}
        self.csrf = ""

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection(self.addr, self.port, timeout=20)
        h = {"Host": host or self.host, "User-Agent": "tests_rename"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        for key, value in r.getheaders():
            if key.lower() == "set-cookie":
                name, _, rest = value.partition("=")
                self.cookies[name] = rest.split(";", 1)[0]
        c.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data

    def set_cookie_headers(self, method, path, **kw):
        c = http.client.HTTPConnection(self.addr, self.port, timeout=20)
        c.request(method, path, headers={"Host": kw.get("host") or self.host})
        r = c.getresponse()
        r.read()
        out = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
        c.close()
        return out

    def login(self, user, password, host=None):
        host = host or self.host
        st, h, body = self.req("GET", "/login", host=host)
        m = re.search(r'name="csrf_token" value="([^"]+)"', body.decode("utf-8", "replace"))
        data = urllib.parse.urlencode({"csrf_token": m.group(1) if m else "", "username": user, "password": password}).encode()
        st, h, body = self.req("POST", "/login", data, {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"https://{host}"}, host=host)
        if st in (302, 303):
            code, hh, raw = self.req("GET", "/api/me", headers={"Accept": "application/json"}, host=host)
            if code == 200:
                self.csrf = json.loads(raw)["csrf"]
        return st

    def api(self, method, path, body=None, host=None, origin=None):
        headers = {"X-Requested-With": "SpotyOFO", "Accept": "application/json", "X-CSRF-Token": self.csrf}
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        st, h, raw = self.req(method, path, data, headers, host=host)
        try:
            return st, json.loads(raw or b"{}"), h
        except ValueError:
            return st, {"_raw": raw[:100]}, h


conn = db.connect()
db.init_schema(conn)
with conn.cursor() as cur:
    cur.execute("SELECT id FROM users WHERE username = 'zzrn_user'")
    for r in cur.fetchall():
        cur.execute("DELETE FROM users WHERE id = %s", (r["id"],))
uid = accounts.create_user(conn, "zzrn_user", "Prueba Renombre", PASS, role="user", status="active")
proc = None
try:
    # ---------- texto visible ----------
    a = Conn(BASE)
    for host in (NEW, OLD):
        st, h, body = a.req("GET", "/login", host=host)
        text = body.decode("utf-8", "replace")
        check(f"login ({host}) 200 y marca «OFO Music»", st == 200 and "OFO Music" in text and "<title>" in text and "OFO Music" in re.search(r"<title>(.*?)</title>", text, re.S).group(1), st)
        check(f"login ({host}) sin «SpotyOFO» visible", "SpotyOFO" not in text, [m for m in re.findall(r".{20}SpotyOFO.{20}", text)][:2])
    st, h, body = a.req("GET", "/manifest.webmanifest")
    man = json.loads(body)
    check("manifest: name y short_name = OFO Music", man.get("name") == "OFO Music" and man.get("short_name") == "OFO Music", (man.get("name"), man.get("short_name")))
    check("manifest: start_url/scope/id relativos (sirven en cualquier host)", man.get("start_url", "").startswith("/") and man.get("scope") == "/" and man.get("id") == "/", (man.get("start_url"), man.get("id")))

    user = Conn(BASE)
    st = user.login("zzrn_user", PASS, host=NEW)
    check("login en music.ofotech.cl", st in (302, 303), st)
    st, h, body = user.req("GET", "/", host=NEW)
    idx = body.decode("utf-8", "replace")
    check("índice: título, descripción, apple-title y logo = OFO Music", all(s in idx for s in ("<title>OFO Music</title>", 'content="OFO Music: ', 'apple-mobile-web-app-title" content="OFO Music"', '<span class="brand-name">OFO Music</span>')), st)
    check("índice sin «SpotyOFO»", "SpotyOFO" not in idx, st)
    # JS: sin «SpotyOFO» en textos visibles (se permiten identificadores internos)
    internal = re.compile(r'spotyofo[-_a-z]*|X-Requested-With|X-SpotyOFO-Offline|Biblioteca_SpotyOFO')
    for name in ("ui.js", "player.js", "offline.js", "radio.js", "main.js", "api.js"):
        text = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        bad = []
        for line in text.splitlines():
            if "SpotyOFO" not in line:
                continue
            rest = re.sub(r'"X-Requested-With"\s*[:,]\s*"SpotyOFO"|setRequestHeader\("X-Requested-With", "SpotyOFO"\)|"X-SpotyOFO-Offline"', "", line)
            if "SpotyOFO" in rest:
                bad.append(line.strip()[:100])
        check(f"{name}: sin «SpotyOFO» visible (solo cabeceras internas)", not bad, bad[:2])
    ui = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
    check("ui: títulos de pestaña y Web Share con OFO Music", 'document.title = "OFO Music"' in ui and "— OFO Music`" in ui and "· OFO Music`" in ui and "en OFO Music`" in ui, "")
    check("ui: enlace compartido usa el origen que da el servidor (canónico) o el actual", "share.origin || location.origin" in ui, "")

    # ---------- identificadores internos intactos ----------
    cookie_headers = Conn(BASE).set_cookie_headers("GET", "/login", host=NEW)
    check("cookie de sesión sigue llamándose spotyofo_session", any(c.startswith("spotyofo_session=") for c in cookie_headers), cookie_headers)
    check("cookie sin atributo Domain (cada host guarda la suya)", cookie_headers and all("domain=" not in c.lower() for c in cookie_headers), cookie_headers)
    check("cookie HttpOnly + SameSite=Lax", cookie_headers and all("httponly" in c.lower() and "samesite=lax" in c.lower() for c in cookie_headers), cookie_headers)
    sw = (ROOT / "pwa" / "sw.js").read_text(encoding="utf-8")
    off = (ROOT / "static" / "js" / "offline.js").read_text(encoding="utf-8")
    check("cachés e IndexedDB conservan sus nombres", "spotyofo-offline-audio" in sw and 'OFFLINE_CACHE = "spotyofo-offline-audio"' in off and 'DB_NAME = "spotyofo-offline"' in off, "")
    check("localStorage conserva spotyofo-theme", 'KEY = "spotyofo-theme"' in (ROOT / "static/js/theme-boot.js").read_text(encoding="utf-8"), "")
    al = json.loads((ROOT / "wellknown" / "assetlinks.json").read_text(encoding="utf-8"))
    check("package id Android sin cambios (cl.ofotech.spotyofo)", al[0]["target"]["package_name"] == "cl.ofotech.spotyofo", al)

    # ---------- doble host (simulado con Host) ----------
    outs = {}
    for host in (NEW, OLD):
        st, h, body = a.req("GET", "/.well-known/assetlinks.json", host=host)
        outs[host] = (st, body)
        check(f"assetlinks 200 JSON sin sesión ({host})", st == 200 and h.get("content-type", "").startswith("application/json") and isinstance(json.loads(body), list), st)
    check("assetlinks idéntico en ambos hosts", outs[NEW][1] == outs[OLD][1], "")
    csp = Conn(BASE).req("GET", "/login", host=NEW)[1].get("content-security-policy", "")
    check("CSP sin hosts propios (solo 'self' + fuentes de Google)", csp and "ofotech" not in csp and "script-src 'self'" in csp, csp[:200])
    st, h, body = user.req("GET", "/api/me", headers={"Origin": f"https://{NEW}"}, host=NEW)
    check("sin cabeceras CORS (Access-Control-*)", st == 200 and not any(k.startswith("access-control-") for k in h), list(h))
    for host in (NEW, OLD):
        st, h, body = a.req("GET", "/s/" + "A" * 22, host=host)
        loc = h.get("location", "")
        check(f"anónimo: /s/<token> -> login con next ({host})", st == 302 and loc.startswith("/login?next=/s/"), (st, loc))
    # Origin por host: el mismo host pasa, el otro host se rechaza (anti-CSRF)
    st, data, _ = user.api("POST", "/api/shares", {"kind": "track", "id": 99999999}, host=NEW, origin=f"https://{NEW}")
    check("API con Origin = host actual no se rechaza por CSRF (404 por pista inexistente)", st == 404, (st, data))
    st, data, _ = user.api("POST", "/api/shares", {"kind": "track", "id": 99999999}, host=NEW, origin=f"https://{OLD}")
    check("API con Origin de OTRO host se rechaza (403)", st == 403, (st, data))
    st, data, _ = user.api("POST", "/api/shares", {"kind": "track", "id": 99999999}, host=OLD, origin=f"https://{OLD}")
    check("lo mismo en el host antiguo: su propio Origin pasa", st == 404, (st, data))
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tracks ORDER BY id LIMIT 1")
        tid = cur.fetchone()["id"]
    st, sh, _ = user.api("POST", "/api/shares", {"kind": "track", "id": tid}, host=NEW, origin=f"https://{NEW}")
    check("compartir: respuesta con 'origin' vacío si no hay host canónico", st == 201 and sh.get("origin") == "", (st, sh))
    tok = sh.get("token", "")
    st, h, body = user.req("GET", "/s/" + tok, host=OLD)
    check("enlace /s/<token> se abre en el host antiguo (sin redirección activa)", st in (302, 200) and (st == 200 or "/#/s/" in h.get("location", "")), (st, h.get("location")))
    st, h, body = user.req("GET", "/s/" + tok, host=NEW)
    check("enlace /s/<token> se abre en music.ofotech.cl", st in (302, 200) and (st == 200 or "/#/s/" in h.get("location", "")), (st, h.get("location")))
    st, h, body = a.req("GET", "/", host=OLD)
    check("sin SPOTY_LEGACY_REDIRECT no hay 301 en el host antiguo", st != 301, st)

    # ---------- segunda instancia con 301 activo ----------
    env = dict(os.environ, SPOTY_PORT=str(args.port2), SPOTY_HOST="127.0.0.1", PYTHONUNBUFFERED="1",
               SPOTY_CANONICAL_HOST=NEW, SPOTY_LEGACY_HOSTS=OLD, SPOTY_LEGACY_REDIRECT="1")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    B2 = f"http://127.0.0.1:{args.port2}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(B2 + "/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
    b = Conn(B2)
    st, h, body = b.req("GET", "/", host=OLD)
    check("301: página de inicio del host antiguo -> https://music.ofotech.cl/", st == 301 and h.get("location") == f"https://{NEW}/", (st, h.get("location")))
    check("301: sin caché (no-store): se puede revertir sin que el navegador lo recuerde", "no-store" in h.get("cache-control", ""), h.get("cache-control"))
    st, h, body = b.req("GET", "/s/Abcdefghijklmnopqrstuv", host=OLD)
    check("301: /s/<token> conserva el token", st == 301 and h.get("location") == f"https://{NEW}/s/Abcdefghijklmnopqrstuv", (st, h.get("location")))
    st, h, body = b.req("GET", "/login?next=/s/Abcdefghijklmnopqrstuv&x=1", host=OLD)
    check("301: conserva la query (?next=...)", st == 301 and h.get("location") == f"https://{NEW}/login?next=/s/Abcdefghijklmnopqrstuv&x=1", (st, h.get("location")))
    st, h, body = b.req("GET", "/s/Abcdefghijklmnopqrstuv", host=OLD.upper())
    check("301: el host se compara sin distinguir mayúsculas", st == 301, st)
    st, h, body = b.req("HEAD", "/", host=OLD)
    check("301: HEAD también", st == 301, st)
    for path in ("/api/health", "/.well-known/assetlinks.json", "/sw.js", "/manifest.webmanifest", "/static/js/theme-boot.js", "/static/favicon.svg"):
        st, h, body = b.req("GET", path, host=OLD)
        check(f"no se redirige en el host antiguo: {path}", st == 200, (st, h.get("location")))
    st, h, body = b.req("GET", "/api/me", headers={"Accept": "application/json"}, host=OLD)
    check("no se redirige: /api/* (anónimo -> 401, la app instalada sigue funcionando)", st == 401, st)
    st, h, body = b.req("POST", "/login", b"x=1", {"Content-Type": "application/x-www-form-urlencoded"}, host=OLD)
    check("no se redirige: POST (nunca se reenvían formularios)", st != 301, st)
    st, h, body = b.req("GET", "/", host=NEW)
    check("el host nuevo no se redirige (302 a login)", st == 302 and h.get("location", "").startswith("/login"), (st, h.get("location")))
    st, h, body = b.req("GET", "/", host=f"127.0.0.1:{args.port2}")
    check("otro host (localhost) no se redirige", st != 301, st)
    b_user = Conn(B2)
    st = b_user.login("zzrn_user", PASS, host=NEW)
    check("instancia 2: login en el host nuevo", st in (302, 303), st)
    st, sh2, _ = b_user.api("POST", "/api/shares", {"kind": "track", "id": tid}, host=OLD, origin=f"https://{OLD}")
    check("instancia 2: desde el host antiguo, el enlace nuevo nace con el host canónico", st == 201 and sh2.get("origin") == f"https://{NEW}", (st, sh2))
    st, h, body = b_user.req("GET", "/api/health", host=OLD)
    check("instancia 2: health sigue accesible en el host antiguo", st == 200 and json.loads(body).get("ok") is True, st)
finally:
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shares WHERE owner_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    check("limpieza: cuenta de prueba borrada", True)

print(f"\n{count} pruebas, " + ("todas OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)