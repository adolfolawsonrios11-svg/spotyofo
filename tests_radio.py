"""Pruebas de la radio en vivo (solo biblioteca estándar + los módulos de la app).

Uso (contra una COPIA de la base; el nombre debe empezar con «test_»):
  python tests_radio.py --db test_spotyofo_radio [--port 5097] [--mock-port 5098]

- Levanta un Radio Browser FALSO en 127.0.0.1 (nunca toca la API real) y una instancia de la app
  apuntando a la copia de la base, con SPOTY_RADIO_SERVERS hacia el falso.
- Crea las cuentas zzradio1 y zzradio2 en la copia y las borra al final (sus favoritos se van en cascada).
- Usa la cuenta demo de la copia (SPOTY_DEMO_PASSWORD de .env) y el botón «Probar demo».
"""

import argparse
import http.cookiejar
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--port", type=int, default=5097)
ap.add_argument("--mock-port", type=int, default=5098)
ap.add_argument("--demo-user", default=None)
args = ap.parse_args()
if not re.fullmatch(r"test_[a-z0-9_]+", args.db):
    sys.exit("La base de pruebas debe llamarse test_<algo> (nunca la base real).")
os.environ["SPOTY_DB_NAME"] = args.db  # antes de importar config: .env no lo pisa

import accounts  # noqa: E402
import config  # noqa: E402,F401
import db as dbmod  # noqa: E402

DEMO_USER = args.demo_user or os.environ.get("SPOTY_DEMO_USER", "demo")
BASE = f"http://127.0.0.1:{args.port}"
MOCK = f"http://127.0.0.1:{args.mock_port}"
PASS = "Radio-Prueba-2026!"
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:400]}"))


# ---------------------------------------------------------------- Radio Browser falso
def U(name):
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "radio-test/" + name))


def st(name, cc, country, state, tags, votes, clicks, url=None, hls=0, favicon=None, codec="MP3", bitrate=128, iso="",
       trend=0):
    key = U(name)
    url = url or f"https://streams.example.test/{key[:8]}/live.mp3"
    return {
        "changeuuid": U("c" + name), "stationuuid": key, "name": name, "url": url, "url_resolved": url,
        "homepage": "https://example.test/", "favicon": favicon if favicon is not None else f"https://img.example.test/{key[:8]}.png",
        "tags": tags, "country": country, "countrycode": cc, "iso_3166_2": iso, "state": state, "language": "spanish",
        "votes": votes, "codec": codec, "bitrate": bitrate, "hls": hls, "lastcheckok": 1, "clickcount": clicks,
        "clicktrend": trend, "ssl_error": 0,
    }


CL = "Chile"
STATIONS = [
    st("Cooperativa", "CL", CL, "Santiago, Región Metropolitana", "news,noticias,talk", 500, 900, trend=3),
    st("Bío-Bío Concepción", "CL", CL, "Bío-Bío", "news,deportes", 300, 700, codec="AAC+", bitrate=0, trend=9),
    st("Radio Biobío Sur", "CL", CL, "Biobío", "pop", 50, 100),
    st("Radio Concepción FM", "CL", CL, "Concepción", "rock,classic rock", 80, 200),
    st("Radio Valpo", "CL", CL, "Valparaiso", "cumbia", 20, 50),
    st("Radio Viña", "CL", CL, "Viña del Mar", "pop,hits", 10, 40),
    st("ADN", "CL", CL, "", "news,deportes", 400, 800, codec="AAC+", bitrate=56, trend=-2),
    st("Radio Insegura", "CL", CL, "Santiago", "pop", 999, 999, url="http://inseguro.example.test/live.mp3"),
    st("Radio HLS", "CL", CL, "Santiago", "pop", 998, 998, url="https://hls.example.test/live.m3u8", hls=1),
    st("Radio Ñuble", "CL", CL, "Región de Ñuble", "folclore,musica chilena", 30, 60),
    st("Radio Temuco", "CL", CL, "Temuco", "romantica", 5, 30, favicon="http://img.example.test/sin-https.png"),
    st("Radio ISO", "CL", CL, "", "jazz", 1, 1, iso="CL-AP"),
    st("Radio Mitre", "AR", "Argentina", "Buenos Aires", "news", 200, 300),
    st("La 100", "AR", "Argentina", "Buenos Aires", "pop", 150, 250),
    st("Rock & Pop", "AR", "Argentina", "Buenos Aires", "rock", 100, 150),
    st("Jazz Radio DE", "DE", "Germany", "Berlin", "jazz", 90, 100),
    st("Smooth Jazz US", "US", "The United States Of America", "California", "jazz,smooth jazz", 120, 140),
    st("Pop France", "FR", "France", "", "pop", 70, 90),
]
BY_UUID = {s["stationuuid"]: s for s in STATIONS}


class Mock:
    fail = False
    hits = []
    lock = threading.Lock()

    @classmethod
    def count(cls, path_prefix, **params):
        with cls.lock:
            n = 0
            for path, query in cls.hits:
                if path.startswith(path_prefix) and all(query.get(k) == v for k, v in params.items()):
                    n += 1
            return n


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        query["_ua"] = self.headers.get("User-Agent", "")
        with Mock.lock:
            Mock.hits.append((parsed.path, query))
        if Mock.fail:
            return self.send_json({"error": "caído"}, 500)
        path = parsed.path
        if path == "/json/stations/search":
            rows = STATIONS
            if query.get("countrycode"):
                rows = [s for s in rows if s["countrycode"] == query["countrycode"]]
            if query.get("name"):
                rows = [s for s in rows if query["name"].lower() in s["name"].lower()]
            if query.get("tag"):
                rows = [s for s in rows if query["tag"].lower() in s["tags"].lower()]
            # is_https se ignora a propósito: el servidor debe filtrar igual.
            order = query.get("order", "name")
            rows = sorted(rows, key=lambda s: s.get(order, s["name"]), reverse=query.get("reverse") == "true")
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 100000))
            return self.send_json(rows[offset: offset + limit])
        if path == "/json/stations/byuuid":
            ids = query.get("uuids", "").split(",")
            return self.send_json([BY_UUID[i] for i in ids if i in BY_UUID])
        if path.startswith("/json/url/"):
            return self.send_json({"ok": True, "url": "x"})
        if path == "/json/countries":
            counts = {}
            for s in STATIONS:
                counts.setdefault(s["countrycode"], [s["country"], 0])[1] += 1
            return self.send_json([{"name": n, "iso_3166_1": c, "stationcount": k} for c, (n, k) in counts.items()])
        if path == "/json/tags":
            tags = {}
            for s in STATIONS:
                for t in s["tags"].split(","):
                    tags[t] = tags.get(t, 0) + 1
            return self.send_json([{"name": t, "stationcount": n} for t, n in sorted(tags.items(), key=lambda x: -x[1])])
        return self.send_json({"error": "no existe"}, 404)


mock = http.server.ThreadingHTTPServer(("127.0.0.1", args.mock_port), Handler)
threading.Thread(target=mock.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- cuentas en la copia
conn = dbmod.connect()
with conn.cursor() as cur:
    cur.execute("SELECT DATABASE() AS d")
    assert cur.fetchone()["d"] == args.db
dbmod.init_schema(conn)
with conn.cursor() as cur:
    cur.execute("DELETE FROM users WHERE username IN ('zzradio1','zzradio2')")
ids = {}
for name in ("zzradio1", "zzradio2"):
    accounts.create_user(conn, name, name.upper(), PASS, role="user", status="active")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (name,))
        ids[name] = cur.fetchone()["id"]
with conn.cursor() as cur:
    cur.execute("SELECT id FROM users WHERE username = %s AND is_demo = 1", (DEMO_USER,))
    demo_row = cur.fetchone()
    if demo_row:
        cur.execute("DELETE FROM radio_favorites WHERE user_id = %s", (demo_row["id"],))

# ---------------------------------------------------------------- app de prueba
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "tests_radio_app.log")
env = dict(os.environ, SPOTY_DB_NAME=args.db, SPOTY_PORT=str(args.port), SPOTY_HOST="127.0.0.1",
           SPOTY_RADIO_SERVERS=MOCK, SPOTY_DEMO_ENABLED="1", PYTHONUNBUFFERED="1", SPOTY_RADIO_TIMEOUT="3")
logf = open(log_path, "w", encoding="utf-8")
app_proc = subprocess.Popen([sys.executable, "app.py"], cwd=os.path.dirname(os.path.abspath(__file__)), env=env,
                            stdout=logf, stderr=subprocess.STDOUT)


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
        st_, h, body = self.raw("GET", page or path)
        m = re.search(r'name="csrf_token" value="([^"]+)"', body.decode("utf-8", "replace"))
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}
        data = urllib.parse.urlencode({"csrf_token": m.group(1) if m else "", **fields}).encode()
        return self.raw("POST", path, data, headers)

    def api(self, method, path, body=None, headers=None, csrf=True):
        hdrs = {"X-Requested-With": "SpotyOFO", "Accept": "application/json"}
        if csrf:
            hdrs["X-CSRF-Token"] = self.csrf
        data = None
        if body is not None:
            hdrs["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        hdrs.update(headers or {})
        st_, h, raw = self.raw(method, path, data, hdrs)
        if h.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        try:
            return st_, json.loads(raw or b"{}")
        except ValueError:
            return st_, {"_raw": raw[:200]}

    def login(self, user, password):
        st_, h, _ = self.form("/login", {"username": user, "password": password})
        if st_ in (302, 303):
            s2, me = self.api("GET", "/api/me")
            self.csrf = me.get("csrf", "")
        return st_


def search(c, **params):
    return c.api("GET", "/api/radio/search?" + urllib.parse.urlencode(params))


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

    # ---------- sin sesión ----------
    anon = Client()
    for method, path in (("GET", "/api/radio/search"), ("GET", "/api/radio/meta"), ("GET", "/api/radio/favorites"),
                         ("POST", "/api/radio/favorites"), ("POST", "/api/radio/click"),
                         ("DELETE", f"/api/radio/favorites/{U('Cooperativa')}")):
        s, d = anon.api(method, path, body={} if method == "POST" else None)
        check(f"sin sesión {method} {path.split('/api/')[1][:24]} -> 401", s == 401, (s, d))
    check("sin sesión no se consultó Radio Browser", len(Mock.hits) == 0, Mock.hits[:3])

    a = Client()
    check("login zzradio1", a.login("zzradio1", PASS) in (302, 303))
    b = Client()
    check("login zzradio2", b.login("zzradio2", PASS) in (302, 303))

    # ---------- meta ----------
    s, meta = a.api("GET", "/api/radio/meta")
    check("meta 200", s == 200, (s, meta))
    regions = {r["key"]: r for r in meta.get("regions", [])}
    check("meta: 16 regiones de Chile de norte a sur", len(meta.get("regions", [])) == 16 and meta["regions"][0]["key"] == "arica" and meta["regions"][-1]["key"] == "magallanes", meta.get("regions"))
    check("meta: Bío-Bío/Biobío/Concepción se juntan en Biobío (3)", regions.get("biobio", {}).get("stations") == 3, regions.get("biobio"))
    check("meta: Valparaiso + Viña del Mar -> Valparaíso (2)", regions.get("valparaiso", {}).get("stations") == 2, regions.get("valparaiso"))
    check("meta: «Región de Ñuble» -> Ñuble, Temuco -> Araucanía, ISO CL-AP -> Arica",
          regions["nuble"]["stations"] == 1 and regions["araucania"]["stations"] == 1 and regions["arica"]["stations"] == 1, regions)
    check("meta: total CL sin http ni HLS (10) y 1 sin región", meta.get("cl_total") == 10 and meta.get("cl_without_region") == 1, (meta.get("cl_total"), meta.get("cl_without_region")))
    check("meta: Latinoamérica incluye AR, MX, BR y no CL", all(c in meta.get("latam", []) for c in ("AR", "MX", "BR")) and "CL" not in meta.get("latam", []), meta.get("latam"))
    check("meta: países y etiquetas desde Radio Browser", any(c["code"] == "DE" for c in meta.get("countries", [])) and any(t["name"] == "pop" for t in meta.get("tags", [])), "")
    genres = {g["key"]: g for g in meta.get("genres", [])}
    check("meta: géneros curados con conteo en Chile (noticias=3)", genres.get("noticias", {}).get("stations_cl") == 3, genres.get("noticias"))
    check("User-Agent SpotyOFO/1.0 hacia Radio Browser", all(q.get("_ua") == "SpotyOFO/1.0" for _, q in Mock.hits) and Mock.hits, Mock.hits[:2])

    # ---------- nacional ----------
    bulk_before = Mock.count("/json/stations/search", countrycode="CL")
    s, d = search(a, scope="nacional")
    names = [x["name"] for x in d.get("stations", [])]
    check("nacional 200 con total 10", s == 200 and d.get("total") == 10, (s, d.get("total"), names))
    check("nacional: solo Chile", all(x["countrycode"] == "CL" for x in d["stations"]), names)
    check("nacional: sin streams http ni HLS", "Radio Insegura" not in names and "Radio HLS" not in names and all(x["url"].startswith("https://") for x in d["stations"]), names)
    check("nacional: orden por clics (Cooperativa, ADN, Bío-Bío)", names[:3] == ["Cooperativa", "ADN", "Bío-Bío Concepción"], names[:4])
    first = d["stations"][0]
    check("emisora trae logo, nombre, país, géneros y bitrate", all(k in first for k in ("favicon", "name", "country", "countrycode", "tags", "codec", "bitrate", "url", "region_label", "favorite")) and not any(k.startswith("_") for k in first), first)
    temuco = next(x for x in d["stations"] if x["name"] == "Radio Temuco")
    check("favicon http se descarta (sin contenido mixto)", temuco["favicon"] == "", temuco["favicon"])
    s, d = search(a, scope="nacional", region="biobio")
    check("región biobio -> 3 emisoras normalizadas", s == 200 and d["total"] == 3 and all(x["region"] == "biobio" for x in d["stations"]), [x["name"] for x in d.get("stations", [])])
    s, d = search(a, scope="nacional", region="otras")
    check("«Sin región declarada» -> ADN", s == 200 and [x["name"] for x in d["stations"]] == ["ADN"], d)
    s, d = search(a, scope="nacional", genre="noticias")
    check("género noticias en Chile -> 3", s == 200 and d["total"] == 3, [x["name"] for x in d.get("stations", [])])
    s, d = search(a, scope="nacional", genre="noticias", region="biobio")
    check("género + región combinados", s == 200 and [x["name"] for x in d["stations"]] == ["Bío-Bío Concepción"], d.get("stations"))
    s, d = search(a, scope="nacional", q="bio")
    check("búsqueda sin tildes «bio» encuentra Bío-Bío y Biobío", s == 200 and {"Bío-Bío Concepción", "Radio Biobío Sur"} <= {x["name"] for x in d["stations"]}, d.get("stations"))
    s, d = search(a, scope="nacional", tag="rock")
    check("etiqueta libre «rock»", s == 200 and [x["name"] for x in d["stations"]] == ["Radio Concepción FM"], d)
    s, d = search(a, scope="nacional", order="votes")
    votes = [x["votes"] for x in d["stations"]]
    check("orden por votos", s == 200 and votes == sorted(votes, reverse=True), votes)
    s, d = search(a, scope="nacional", order="name")
    nm = [x["name"] for x in d["stations"]]
    check("orden por nombre", s == 200 and nm[0] == "ADN", nm)
    s, d = search(a, scope="nacional", order="trend")
    check("orden por tendencia", s == 200 and d["stations"][0]["name"] == "Bío-Bío Concepción", [x["name"] for x in d["stations"]][:3])
    s, p1 = search(a, scope="nacional", limit=4, offset=0)
    s2, p2 = search(a, scope="nacional", limit=4, offset=4)
    s3, p3 = search(a, scope="nacional", limit=4, offset=8)
    u1, u2, u3 = ({x["stationuuid"] for x in p["stations"]} for p in (p1, p2, p3))
    check("paginación sin repetir y has_more correcto", len(u1) == 4 and len(u2) == 4 and len(u3) == 2 and not (u1 & u2) and not (u2 & u3)
          and p1["has_more"] and p2["has_more"] and not p3["has_more"], (len(u1), len(u2), len(u3), p3.get("has_more")))
    check("caché: Chile se pidió a Radio Browser una sola vez", Mock.count("/json/stations/search", countrycode="CL") - bulk_before <= 1 and Mock.count("/json/stations/search", countrycode="CL") == 1,
          Mock.count("/json/stations/search", countrycode="CL"))
    check("pide is_https=true y hidebroken=true", Mock.count("/json/stations/search", countrycode="CL", is_https="true", hidebroken="true") == 1, "")

    # ---------- regional ----------
    s, d = search(a, scope="regional", country="AR")
    check("regional AR -> 3 emisoras argentinas", s == 200 and d["total"] == 3 and all(x["countrycode"] == "AR" for x in d["stations"]), d)
    s, d = search(a, scope="regional")
    check("regional sin país usa AR", s == 200 and d.get("country") == "AR", d.get("country"))
    s, d = search(a, scope="regional", country="DE")
    check("regional con país fuera de Latinoamérica -> 400", s == 400, (s, d))
    s, d = search(a, scope="regional", country="AR", region="biobio")
    check("región solo vale en Nacional -> 400", s == 400, s)

    # ---------- mundial ----------
    s, d = search(a, scope="mundial", genre="jazz")
    nm = {x["name"] for x in d.get("stations", [])}
    check("mundial género jazz -> DE, US y Radio ISO", s == 200 and nm == {"Jazz Radio DE", "Smooth Jazz US", "Radio ISO"}, nm)
    check("mundial manda tag=jazz y orden clickcount", Mock.count("/json/stations/search", tag="jazz", order="clickcount", reverse="true") == 1, "")
    search(a, scope="mundial", genre="jazz")
    search(b, scope="mundial", genre="jazz")
    check("caché mundial compartida: 3 búsquedas iguales = 1 petición", Mock.count("/json/stations/search", tag="jazz") == 1, Mock.count("/json/stations/search", tag="jazz"))
    s, d = search(a, scope="mundial", q="pop", country="FR")
    check("mundial por nombre y país", s == 200 and [x["name"] for x in d["stations"]] == ["Pop France"], d)
    s, d = search(a, scope="mundial", limit=3)
    nm = [x["name"] for x in d.get("stations", [])]
    check("mundial paginado: descarta http/HLS aunque la API los mande, has_more y next_offset", s == 200 and nm == ["Cooperativa"] and d["has_more"] and d["next_offset"] == 3 and d["total"] is None, d)
    s, d = search(a, scope="mundial", limit=3, offset=3)
    check("mundial página 2 desde next_offset", s == 200 and [x["name"] for x in d["stations"]] == ["ADN", "Bío-Bío Concepción", "Radio Mitre"], [x["name"] for x in d.get("stations", [])])
    s, d = search(a, scope="mundial", country="xx1")
    check("país inválido -> 400", s == 400, s)

    # ---------- validación ----------
    s, _ = search(a, scope="espacio")
    check("scope inválido -> 400", s == 400, s)
    s, _ = search(a, scope="nacional", order="random")
    check("orden inválido -> 400", s == 400, s)
    s, _ = search(a, scope="nacional", genre="xx")
    check("género inválido -> 400", s == 400, s)
    s, _ = search(a, scope="nacional", limit="abc")
    check("limit no numérico -> 400", s == 400, s)
    s, d = search(a, scope="nacional", limit=99999)
    check("limit se acota a 60", s == 200 and d["limit"] == 60, d.get("limit"))
    s, h, raw = a.raw("GET", "/api/radio/search?scope=nacional", headers={"X-Requested-With": "SpotyOFO"})
    check("respuesta de radio con Cache-Control no-store", h.get("Cache-Control") == "no-store", h.get("Cache-Control"))

    # ---------- favoritos ----------
    coop, adn, mitre = U("Cooperativa"), U("ADN"), U("Radio Mitre")
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": coop})
    check("agregar favorito -> 201", s == 201 and d.get("added") is True and d["favorite"]["name"] == "Cooperativa", (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": coop})
    check("agregar repetido -> 200 sin duplicar", s == 200 and d.get("added") is False, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": mitre.upper()})
    check("agregar otra (uuid en mayúsculas se normaliza)", s == 201, (s, d))
    s, d = a.api("GET", "/api/radio/favorites")
    fav_names = [x["name"] for x in d.get("favorites", [])]
    check("listar favoritos de zzradio1 (2, la última primero)", s == 200 and fav_names == ["Radio Mitre", "Cooperativa"], fav_names)
    f0 = d["favorites"][1]
    check("favorito guarda nombre, url, logo, país, géneros", f0["url"].startswith("https://") and f0["favicon"].startswith("https://") and f0["countrycode"] == "CL" and "news" in f0["tags"], f0)
    with conn.cursor() as cur:
        cur.execute("SELECT name, url FROM radio_favorites WHERE user_id=%s AND stationuuid=%s", (ids["zzradio1"], coop))
        row = cur.fetchone()
    check("los datos guardados vienen del servidor, no del navegador", row and row["url"] == BY_UUID[coop]["url_resolved"], row)
    s, d = search(a, scope="nacional", q="cooperativa")
    check("búsqueda marca favorite=true para su dueño", s == 200 and d["stations"][0]["favorite"] is True, d)
    s, d = search(b, scope="nacional", q="cooperativa")
    check("otra cuenta ve favorite=false", s == 200 and d["stations"][0]["favorite"] is False, d)
    s, d = b.api("GET", "/api/radio/favorites")
    check("aislamiento: zzradio2 no ve los favoritos de zzradio1", s == 200 and d["favorites"] == [], d)
    s, d = b.api("DELETE", f"/api/radio/favorites/{coop}")
    check("zzradio2 no puede borrar el favorito de zzradio1", s == 200 and d.get("removed") == 0, (s, d))
    s, d = a.api("GET", "/api/radio/favorites")
    check("el favorito de zzradio1 sigue ahí", len(d.get("favorites", [])) == 2, d)
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": "no-es-uuid"})
    check("uuid inválido -> 400", s == 400, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": str(uuidlib.uuid4())})
    check("emisora inexistente -> 404", s == 404, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": U("Radio Insegura")})
    check("emisora http -> 422 (no se guarda)", s == 422, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": U("Radio HLS")})
    check("emisora HLS -> 422", s == 422, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"name": "x"})
    check("sin stationuuid -> 400", s == 400, s)
    s, d = a.api("DELETE", f"/api/radio/favorites/{mitre}")
    check("quitar favorito", s == 200 and d.get("removed") == 1, (s, d))
    s, d = a.api("DELETE", "/api/radio/favorites/zzz")
    check("quitar con uuid inválido -> 400", s == 400, s)

    # ---------- CSRF ----------
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": adn}, csrf=False)
    check("POST favorito sin token CSRF -> 403", s == 403, (s, d))
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": adn}, headers={"X-CSRF-Token": "falso"})
    check("POST favorito con token falso -> 403", s == 403, s)
    s, d = a.api("POST", "/api/radio/favorites", {"stationuuid": adn}, headers={"Origin": "https://evil.example"})
    check("POST favorito desde otro origen -> 403", s == 403, s)
    s, d = a.api("DELETE", f"/api/radio/favorites/{coop}", headers={"Sec-Fetch-Site": "cross-site"})
    check("DELETE favorito cross-site -> 403", s == 403, s)
    s, d = a.api("POST", "/api/radio/click", {"stationuuid": adn}, csrf=False)
    check("POST click sin token CSRF -> 403", s == 403, s)
    s, d = a.api("GET", "/api/radio/favorites")
    check("tras los intentos CSRF solo queda Cooperativa", [x["name"] for x in d.get("favorites", [])] == ["Cooperativa"], d)

    # ---------- clic en segundo plano ----------
    s, d = a.api("POST", "/api/radio/click", {"stationuuid": adn})
    check("click -> 202 contado", s == 202 and d.get("counted") is True, (s, d))
    for _ in range(20):
        if Mock.count(f"/json/url/{adn}"):
            break
        time.sleep(0.1)
    check("click llegó a /json/url/<uuid> de Radio Browser", Mock.count(f"/json/url/{adn}") == 1, Mock.count("/json/url/"))
    s, d = a.api("POST", "/api/radio/click", {"stationuuid": adn})
    time.sleep(0.4)
    check("click repetido de la misma cuenta no se reenvía (1 por hora)", s == 202 and d.get("counted") is False and Mock.count(f"/json/url/{adn}") == 1, d)
    s, d = a.api("POST", "/api/radio/click", {"stationuuid": "x"})
    check("click con uuid inválido -> 400", s == 400, s)

    # ---------- demo ----------
    dm = Client()
    me = {}
    s, h, _ = dm.form("/login/demo", {}, page="/login")
    if s in (302, 303):
        s2, me = dm.api("GET", "/api/me")
        dm.csrf = me.get("csrf", "")
    check("demo entra con «Probar demo»", s in (302, 303) and me.get("demo") is True, s)
    s, d = search(dm, scope="nacional", region="biobio")
    check("demo puede ver radios", s == 200 and d["total"] == 3, s)
    s, d = dm.api("GET", "/api/radio/meta")
    check("demo ve meta y sabe que es demo", s == 200 and d.get("demo") is True, s)
    s, d = dm.api("GET", "/api/radio/favorites")
    check("demo lista Mis radios (vacía)", s == 200 and d["favorites"] == [] and d.get("demo") is True, d)
    s, d = dm.api("POST", "/api/radio/favorites", {"stationuuid": coop})
    check("demo no puede guardar favoritos -> 403", s == 403 and "demo" in d.get("error", "").lower(), (s, d))
    s, d = dm.api("DELETE", f"/api/radio/favorites/{coop}")
    check("demo no puede quitar favoritos -> 403", s == 403, s)
    before = Mock.count("/json/url/")
    s, d = dm.api("POST", "/api/radio/click", {"stationuuid": coop})
    time.sleep(0.4)
    check("demo escucha, pero su clic no suma en Radio Browser", s == 202 and d.get("counted") is False and Mock.count("/json/url/") == before, d)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM radio_favorites rf JOIN users u ON u.id = rf.user_id WHERE u.is_demo = 1")
        check("la demo no tiene filas en radio_favorites", cur.fetchone()["n"] == 0)

    # ---------- Radio Browser caído ----------
    Mock.fail = True
    s, d = search(a, scope="nacional", region="biobio")
    check("con Radio Browser caído, lo cacheado se sigue sirviendo", s == 200 and d["total"] == 3, s)
    s, d = search(a, scope="mundial", q="nueva")
    check("búsqueda nueva con Radio Browser caído -> 503 con mensaje amable", s == 503 and "no está disponible" in d.get("error", ""), (s, d))
    hits = len(Mock.hits)
    s, d = search(a, scope="mundial", q="nueva")
    check("fallo recordado 1 min: no se martilla la API", s == 503 and len(Mock.hits) == hits, len(Mock.hits) - hits)
    Mock.fail = False

    # ---------- página, CSP y archivos ----------
    s, h, body = a.raw("GET", "/")
    html = body.decode("utf-8", "replace")
    csp = h.get("Content-Security-Policy", "")
    check("CSP: media-src 'self' blob: https:", "media-src 'self' blob: https:" in csp, csp)
    check("CSP: img-src con https: (logos)", "img-src 'self' data: blob: https:" in csp, csp)
    check("CSP: script-src y connect-src sin ampliar", "script-src 'self';" in csp and "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com;" in csp and "http:" not in csp, csp)
    check("enlace Radio en la barra (escritorio y móvil)", 'href="#/radio"' in html and 'data-nav="radio"' in html and 'class="nav-short">Radio<' in html, "")
    check("insignia EN VIVO en la barra y en Ahora suena", 'id="p-live"' in html and 'id="now-live"' in html, "")
    m = re.search(r'/static/js/main\.js\?v=([\w.-]+)', html)
    s, h, body = a.raw("GET", f"/static/js/radio.js?v={m.group(1) if m else ''}")
    js = body.decode("utf-8", "replace")
    check("radio.js se sirve y usa la API", s == 200 and "radioSearch" in js and "Mis radios" in js, s)
    s, h, body = a.raw("GET", "/static/js/player.js")
    pj = body.decode("utf-8", "replace")
    check("player.js: modo radio sin Web Audio, con aviso y EN VIVO", "playRadio" in pj and "createMediaElementSource" in pj and "radioEl" in pj and "no responde ahora" in pj and "EN VIVO" in pj, "")
    s, h, body = anon.raw("GET", "/static/js/radio.js")
    check("radio.js es público como el resto del JS (sin datos)", s == 200, s)

    # ---------- borrado en cascada ----------
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username IN ('zzradio1','zzradio2')")
        cur.execute("SELECT COUNT(*) AS n FROM radio_favorites WHERE user_id IN (%s, %s)", (ids["zzradio1"], ids["zzradio2"]))
        check("borrar la cuenta borra sus radios (ON DELETE CASCADE)", cur.fetchone()["n"] == 0)
finally:
    app_proc.terminate()
    try:
        app_proc.wait(10)
    except subprocess.TimeoutExpired:
        app_proc.kill()
    logf.close()
    mock.shutdown()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username IN ('zzradio1','zzradio2')")
    conn.close()

log_text = open(log_path, encoding="utf-8", errors="replace").read()
check("el log de la app no tiene errores no controlados", "Traceback" not in log_text and "Error no controlado" not in log_text, log_text[-600:])
print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
