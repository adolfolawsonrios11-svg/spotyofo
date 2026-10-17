"""Radio en vivo: proxy con caché hacia la API pública de Radio Browser (radio-browser.info).

- El navegador nunca habla con Radio Browser: pide /api/radio/* a SpotyOFO y el servidor
  responde desde una caché en memoria (6 h) para no saturar la API ni el único worker.
- El audio NO pasa por el servidor: el reproductor abre directamente el stream https
  (url_resolved) de cada emisora. Solo se ofrecen streams https (sin contenido mixto) y sin HLS.
- Una sola petición a la vez por clave (single-flight), como máximo 3 peticiones simultáneas
  hacia afuera, y si Radio Browser falla se sirve la copia vencida (stale-if-error).
"""

import json
import logging
import os
import random
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("spotyofo.radio")

USER_AGENT = "SpotyOFO/1.0"
TTL = int(os.environ.get("SPOTY_RADIO_TTL", str(6 * 3600)))
STALE_RETRY = 300          # tras un fallo, la copia vencida se reutiliza 5 min antes de reintentar
NEGATIVE_TTL = 60          # un fallo sin copia se recuerda 1 min (no martillar una API caída)
TIMEOUT = float(os.environ.get("SPOTY_RADIO_TIMEOUT", "8"))
BULK_LIMIT = 3000          # emisoras por país (Chile tiene ~580 con hidebroken)
MAX_FAVORITES = 200
PAGE_MAX = 60
OFFSET_MAX = 5000
DEFAULT_SERVERS = [
    "https://de1.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
]

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CC_RE = re.compile(r"^[A-Z]{2}$")

LATAM = ["AR", "BO", "BR", "CO", "CR", "CU", "DO", "EC", "SV", "GT", "HN", "MX", "NI", "PA",
         "PY", "PE", "PR", "UY", "VE"]
ORDERS = {
    # clave pública -> (campo de Radio Browser, invertido)
    "clicks": ("clickcount", True),
    "votes": ("votes", True),
    "trend": ("clicktrend", True),
    "name": ("name", False),
}
SCOPES = ("nacional", "regional", "mundial")


class RadioError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


UNAVAILABLE = "La radio no está disponible ahora. Intenta de nuevo en unos minutos."


def fold(text):
    """minúsculas, sin tildes y sin signos: «Bío-Bío» -> «bio bio»."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


# ---------------------------------------------------------------- regiones de Chile
# (clave, nombre, ISO 3166-2, alias). Radio Browser guarda en «state» lo que escribió quien
# agregó la emisora: a veces la región, a veces la ciudad y casi nunca el código ISO.
CL_REGIONS = [
    ("arica", "Arica y Parinacota", "CL-AP", ["arica y parinacota", "arica", "parinacota", "putre", "xv region"]),
    ("tarapaca", "Tarapacá", "CL-TA", ["tarapaca", "iquique", "alto hospicio", "pozo almonte", "i region"]),
    ("antofagasta", "Antofagasta", "CL-AN", ["antofagasta", "calama", "tocopilla", "mejillones", "taltal",
                                             "san pedro de atacama", "maria elena", "ii region"]),
    ("atacama", "Atacama", "CL-AT", ["atacama", "copiapo", "vallenar", "huasco", "freirina", "caldera",
                                     "chanaral", "diego de almagro", "tierra amarilla", "iii region"]),
    ("coquimbo", "Coquimbo", "CL-CO", ["coquimbo", "la serena", "ovalle", "los vilos", "illapel", "vicuna",
                                       "salamanca", "andacollo", "combarbala", "monte patria", "iv region"]),
    ("valparaiso", "Valparaíso", "CL-VS", ["valparaiso", "vina del mar", "vina", "quillota", "san felipe",
                                           "los andes", "san antonio", "quilpue", "villa alemana", "la calera",
                                           "limache", "casablanca", "cartagena", "quintero", "la ligua",
                                           "isla de pascua", "rapa nui", "llay llay", "llaillay", "con con",
                                           "concon", "petorca", "cabildo", "v region"]),
    ("metropolitana", "Metropolitana", "CL-RM", ["metropolitana", "region metropolitana", "metropolitana de santiago",
                                                 "rm", "santiago", "santiago de chile", "maipu", "melipilla",
                                                 "curacavi", "puente alto", "san bernardo", "providencia",
                                                 "las condes", "nunoa", "talagante", "buin", "pudahuel",
                                                 "la florida", "colina", "penaflor", "paine", "quilicura",
                                                 "estacion central", "recoleta", "independencia", "la reina",
                                                 "vitacura", "lo barnechea", "san miguel", "la cisterna",
                                                 "el bosque", "la pintana", "renca", "cerrillos", "macul",
                                                 "san joaquin", "lampa", "tiltil", "pirque", "isla de maipo"]),
    ("ohiggins", "O'Higgins", "CL-LI", ["ohiggins", "o higgins", "libertador", "libertador general bernardo o higgins",
                                        "libertador bernardo o higgins", "rancagua", "san fernando", "santa cruz",
                                        "pichilemu", "rengo", "machali", "san vicente", "san vicente de tagua tagua",
                                        "chimbarongo", "graneros", "peumo", "vi region"]),
    ("maule", "Maule", "CL-ML", ["maule", "talca", "curico", "linares", "cauquenes", "constitucion", "villa alegre",
                                 "molina", "parral", "san javier", "teno", "licanten", "vii region"]),
    ("nuble", "Ñuble", "CL-NB", ["nuble", "chillan", "chillan viejo", "san carlos", "bulnes", "yungay",
                                 "san ignacio", "el carmen", "quirihue", "coihueco", "coelemu", "pemuco", "xvi region"]),
    ("biobio", "Biobío", "CL-BI", ["biobio", "bio bio", "concepcion", "talcahuano", "los angeles", "coronel", "lota",
                                   "canete", "laja", "santa juana", "chiguayante", "san pedro de la paz", "tome",
                                   "hualpen", "arauco", "curanilahue", "lebu", "mulchen", "nacimiento", "penco",
                                   "cabrero", "yumbel", "santa barbara", "hualqui", "florida", "viii region"]),
    ("araucania", "La Araucanía", "CL-AR", ["araucania", "la araucania", "temuco", "lautaro", "carahue", "pucon",
                                            "villarrica", "angol", "victoria", "nueva imperial", "padre las casas",
                                            "traiguen", "collipulli", "loncoche", "curacautin", "pitrufquen",
                                            "ix region"]),
    ("losrios", "Los Ríos", "CL-LR", ["los rios", "valdivia", "la union", "panguipulli", "rio bueno", "lanco",
                                      "paillaco", "futrono", "mariquina", "xiv region"]),
    ("loslagos", "Los Lagos", "CL-LL", ["los lagos", "puerto montt", "osorno", "castro", "ancud", "chiloe",
                                        "puerto varas", "frutillar", "calbuco", "quellon", "chaiten",
                                        "purranque", "rio negro", "x region"]),
    ("aysen", "Aysén", "CL-AI", ["aysen", "aisen", "coyhaique", "coihaique", "puerto aysen", "xi region"]),
    ("magallanes", "Magallanes", "CL-MA", ["magallanes", "punta arenas", "puerto natales", "porvenir", "antartica",
                                           "magallanes y la antartica chilena", "xii region"]),
]
REGION_BY_KEY = {key: (key, label, iso) for key, label, iso, _ in CL_REGIONS}
_REGION_BY_ISO = {iso: key for key, _, iso, _ in CL_REGIONS}
_REGION_EXACT = {}
for _key, _label, _iso, _aliases in CL_REGIONS:
    for _alias in [_label] + _aliases:
        _REGION_EXACT.setdefault(fold(_alias), _key)
# Búsqueda por palabra dentro del texto: primero nombres de región, luego ciudades (alias largos primero).
_REGION_WORDS = sorted(
    ((fold(alias), key, rank) for key, label, _, aliases in CL_REGIONS
     for rank, alias in enumerate([label] + aliases) if len(fold(alias)) > 3),
    key=lambda item: (item[2] > 1, -len(item[0])),
)
_REGION_PREFIX = re.compile(r"^(?:region(?: de(?:l)?)?|provincia(?: de(?:l)?)?|comuna(?: de(?:l)?)?)\s+")


def cl_region(state, iso=""):
    """Clave de región chilena para el «state» que trae Radio Browser, o "" si no se reconoce."""
    iso = str(iso or "").strip().upper()
    if iso in _REGION_BY_ISO:
        return _REGION_BY_ISO[iso]
    raw = str(state or "")
    whole = _REGION_PREFIX.sub("", fold(raw))
    if not whole:
        return ""
    if whole in _REGION_EXACT:
        return _REGION_EXACT[whole]
    for part in re.split(r"[,;/|()\-]+", raw):
        part = _REGION_PREFIX.sub("", fold(part))
        if part in _REGION_EXACT:
            return _REGION_EXACT[part]
    for alias, key, _ in _REGION_WORDS:
        if re.search(rf"\b{re.escape(alias)}\b", whole):
            return key
    return ""


# ---------------------------------------------------------------- géneros
# (clave, etiqueta, etiqueta principal en Radio Browser para «Mundial», etiquetas equivalentes)
GENRES = [
    ("noticias", "Noticias", "news", ["news", "noticias", "talk", "radio hablada", "hablada", "local news",
                                      "informativa", "informacion", "actualidad", "news talk", "talk radio",
                                      "musica y noticias", "periodismo", "magazine"]),
    ("pop", "Pop", "pop", ["pop", "hits", "top40", "top 40", "pop music", "musica pop", "pop latino", "latin pop",
                           "pop rock", "contemporary hits", "chr", "pop en espanol", "adult contemporary"]),
    ("rock", "Rock", "rock", ["rock", "classic rock", "rock clasico", "pop rock", "alternative", "alternative rock",
                              "indie", "indie rock", "hard rock", "metal", "heavy metal", "rock en espanol",
                              "rock nacional", "rock latino", "punk", "grunge"]),
    ("clasicos", "Clásicos 70-80-90", "80s", ["80s", "90s", "70s", "60s", "oldies", "clasicos", "retro", "80 s",
                                              "90 s", "2000s", "decades", "classic hits", "anos 80", "anos 90",
                                              "ochentas", "noventas", "musica del recuerdo", "recuerdos"]),
    ("romantica", "Romántica", "romantic", ["romantica", "musica romantica", "romantic", "baladas", "balada",
                                            "ballads", "love songs", "boleros", "bolero", "baladas romanticas"]),
    ("tropical", "Cumbia y tropical", "cumbia", ["cumbia", "tropical", "salsa", "merengue", "bachata", "reggaeton",
                                                  "urbano", "musica tropical", "cumbia chilena", "latin", "latino",
                                                  "musica latina", "ranchera", "rancheras", "regional mexicano"]),
    ("jazz", "Jazz y blues", "jazz", ["jazz", "blues", "soul", "smooth jazz", "swing", "bossa nova", "funk"]),
    ("clasica", "Clásica", "classical", ["classical", "classical music", "musica clasica", "clasica", "opera",
                                         "orchestra", "orquesta", "barroco", "baroque", "symphony"]),
    ("electronica", "Electrónica", "electronic", ["electronic", "electronica", "dance", "house", "edm", "techno",
                                                   "trance", "lounge", "chillout", "chill", "deep house", "ambient"]),
    ("deportes", "Deportes", "sports", ["deportes", "sports", "sport", "futbol", "football", "deportiva"]),
    ("folclore", "Folclore", "folk", ["folclore", "folklore", "folk", "musica chilena", "chilean music", "andina",
                                      "cueca", "musica latinoamericana", "raiz", "folclor", "musica folclorica"]),
    ("cristiana", "Cristiana", "christian", ["cristiana", "radio cristiana", "christian", "gospel", "catolica",
                                             "catholic", "religious", "religion", "evangelica", "evangelical",
                                             "christian music", "musica cristiana"]),
    ("cultural", "Cultural y universitaria", "university radio", ["university radio", "radio universitaria",
                                                                  "universitaria", "cultural", "cultura",
                                                                  "college radio", "educativa", "educational",
                                                                  "community radio", "radio comunitaria",
                                                                  "comunitaria", "arte"]),
]
GENRE_BY_KEY = {key: (key, label, primary, {fold(tag) for tag in tags}) for key, label, primary, tags in GENRES}


def station_matches_genre(station, genre_key):
    _, _, _, tags = GENRE_BY_KEY[genre_key]
    for tag in station["_tagfold"]:
        if tag in tags:
            return True
        for alias in tags:
            if len(alias) > 3 and re.search(rf"\b{re.escape(alias)}\b", tag):
                return True
    return False


# ---------------------------------------------------------------- caché
class TTLCache:
    """Caché en memoria acotada: valor + vencimiento. Guarda la copia vencida para stale-if-error."""

    def __init__(self, max_items=400):
        self._lock = threading.Lock()
        self._data = OrderedDict()
        self._key_locks = {}
        self.max_items = max_items
        self.stats = {"hits": 0, "misses": 0, "stale": 0, "errors": 0}

    def _get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is not None:
                self._data.move_to_end(key)
            return item

    def _put(self, key, value, ttl, error=None):
        with self._lock:
            self._data[key] = (time.time() + ttl, value, error)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def _key_lock(self, key):
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                if len(self._key_locks) > self.max_items * 2:
                    self._key_locks = {k: v for k, v in self._key_locks.items() if v.locked()}
                lock = self._key_locks[key] = threading.Lock()
            return lock

    def fetch(self, key, loader, ttl=TTL):
        """Devuelve (valor, vencido). Un solo loader por clave; si falla usa la copia vencida."""
        item = self._get(key)
        if item and item[0] > time.time():
            if item[2] is not None:
                raise item[2]
            self.stats["hits"] += 1
            return item[1], False
        with self._key_lock(key):
            item = self._get(key)  # otro hilo pudo cargarla mientras esperábamos
            if item and item[0] > time.time():
                if item[2] is not None:
                    raise item[2]
                self.stats["hits"] += 1
                return item[1], False
            self.stats["misses"] += 1
            try:
                value = loader()
            except RadioError as error:
                self.stats["errors"] += 1
                if item and item[2] is None:
                    self.stats["stale"] += 1
                    self._put(key, item[1], STALE_RETRY)
                    return item[1], True
                self._put(key, None, NEGATIVE_TTL, error)
                raise
            self._put(key, value, ttl)
            return value, False

    def clear(self):
        with self._lock:
            self._data.clear()


cache = TTLCache()
_seen_lock = threading.Lock()
_seen = OrderedDict()  # stationuuid -> emisora normalizada (para guardar favoritos sin creer al navegador)
SEEN_MAX = 8000


def _remember(stations):
    with _seen_lock:
        for station in stations:
            _seen[station["stationuuid"]] = station
            _seen.move_to_end(station["stationuuid"])
        while len(_seen) > SEEN_MAX:
            _seen.popitem(last=False)


# ---------------------------------------------------------------- servidores y peticiones
_servers_lock = threading.Lock()
_servers = None
_good = 0
_upstream = threading.BoundedSemaphore(3)


def _discover():
    """Servidores vigentes según el DNS de all.api.radio-browser.info (lo que recomienda la API)."""
    names = set()
    try:
        for info in socket.getaddrinfo("all.api.radio-browser.info", 443, proto=socket.IPPROTO_TCP):
            try:
                host = socket.gethostbyaddr(info[4][0])[0].lower().rstrip(".")
            except OSError:
                continue
            if re.fullmatch(r"[a-z0-9-]+\.api\.radio-browser\.info", host):
                names.add(f"https://{host}")
    except OSError:
        return []
    return sorted(names)


def servers():
    global _servers
    with _servers_lock:
        if _servers is not None:
            return list(_servers)
    configured = [s.strip().rstrip("/") for s in os.environ.get("SPOTY_RADIO_SERVERS", "").split(",") if s.strip()]
    if configured:
        merged = configured
    else:
        found = _discover()
        random.shuffle(found)  # repartir la carga entre servidores, como pide la API
        merged = found + [s for s in DEFAULT_SERVERS if s not in found]
    with _servers_lock:
        _servers = merged
        return list(_servers)


def _request(path, params=None, read_json=True):
    """GET a Radio Browser con conmutación entre servidores. Lanza RadioError si todos fallan."""
    global _good
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    pool = servers()
    if not _upstream.acquire(timeout=TIMEOUT * 2):
        raise RadioError("La radio está muy solicitada ahora. Intenta de nuevo en un momento.")
    try:
        start = _good % len(pool)
        for step in range(len(pool)):
            index = (start + step) % len(pool)
            url = pool[index] + path + query
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                    body = response.read(16 * 1024 * 1024)
                _good = index
                return json.loads(body.decode("utf-8")) if read_json else None
            except (urllib.error.URLError, OSError, ValueError) as error:
                log.warning("Radio Browser %s falló en %s: %s", path, pool[index], type(error).__name__)
        raise RadioError(UNAVAILABLE)
    finally:
        _upstream.release()


# ---------------------------------------------------------------- normalización
def _int(value, low=0, high=10**9):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _clean(value, size):
    return " ".join(str(value or "").split())[:size]


def normalize_station(raw):
    """Emisora lista para el navegador, o None si no se puede escuchar aquí (no https, HLS, rota)."""
    if not isinstance(raw, dict):
        return None
    uuid = str(raw.get("stationuuid") or "").lower()
    if not UUID_RE.match(uuid):
        return None
    url = str(raw.get("url_resolved") or raw.get("url") or "").strip()
    if not url.lower().startswith("https://") or len(url) > 1000 or any(c in url for c in "\r\n\t \"'<>"):
        return None
    path = urllib.parse.urlparse(url).path.lower()
    if _int(raw.get("hls")) == 1 or path.endswith((".m3u8", ".m3u", ".pls", ".asx")):
        return None
    if raw.get("lastcheckok") is not None and _int(raw.get("lastcheckok")) == 0:
        return None
    name = _clean(raw.get("name"), 160)
    if not name:
        return None
    favicon = str(raw.get("favicon") or "").strip()
    if not favicon.lower().startswith("https://") or len(favicon) > 1000 or any(c in favicon for c in "\r\n\t \"'<>"):
        favicon = ""
    homepage = str(raw.get("homepage") or "").strip()
    if not homepage.lower().startswith("https://") or len(homepage) > 500 or any(c in homepage for c in "\r\n\t \"'<>"):
        homepage = ""
    tags, seen_tags = [], set()
    for tag in str(raw.get("tags") or "").split(","):
        tag = _clean(tag, 40)
        if tag and fold(tag) not in seen_tags:
            seen_tags.add(fold(tag))
            tags.append(tag)
        if len(tags) >= 8:
            break
    countrycode = str(raw.get("countrycode") or "").upper()[:2]
    if not CC_RE.match(countrycode):
        countrycode = ""
    state = _clean(raw.get("state"), 100)
    region = cl_region(state, raw.get("iso_3166_2")) if countrycode == "CL" else ""
    return {
        "stationuuid": uuid,
        "name": name,
        "url": url,
        "favicon": favicon,
        "homepage": homepage,
        "tags": tags,
        "country": _clean(raw.get("country"), 80),
        "countrycode": countrycode,
        "state": state,
        "region": region,
        "region_label": REGION_BY_KEY[region][1] if region else "",
        "codec": _clean(raw.get("codec"), 20).upper(),
        "bitrate": _int(raw.get("bitrate"), 0, 9999),
        "votes": _int(raw.get("votes")),
        "clickcount": _int(raw.get("clickcount")),
        "clicktrend": _int(raw.get("clicktrend"), -10**9),
        "_tagfold": [fold(tag) for tag in tags],
        "_namefold": fold(name),
    }


def public(station, favorites=()):
    data = {key: value for key, value in station.items() if not key.startswith("_")}
    data["favorite"] = station["stationuuid"] in favorites
    return data


def _normalize_all(rows):
    out, seen = [], set()
    for raw in rows if isinstance(rows, list) else []:
        station = normalize_station(raw)
        if station and station["stationuuid"] not in seen:
            seen.add(station["stationuuid"])
            out.append(station)
    _remember(out)
    return out


# ---------------------------------------------------------------- cargas con caché
def country_stations(cc):
    """Todas las emisoras https de un país (una petición, 6 h de caché). Devuelve (lista, vencida)."""
    def load():
        rows = _request("/json/stations/search", {
            "countrycode": cc, "hidebroken": "true", "is_https": "true",
            "order": "clickcount", "reverse": "true", "limit": str(BULK_LIMIT),
        })
        return _normalize_all(rows)
    stations, stale = cache.fetch(("country", cc), load)
    if stale:
        _remember(stations)
    return stations, stale


def _sort_key(order):
    if order == "name":
        return lambda s: (s["_namefold"], -s["clickcount"])
    field = ORDERS[order][0]
    return lambda s: (-s[field], -s["clickcount"], s["_namefold"])


def search(scope, q="", country="", region="", genre="", tag="", order="clicks", offset=0, limit=30):
    """Búsqueda paginada. Nacional/Regional filtran en memoria; Mundial consulta (con caché) a Radio Browser."""
    q_fold, tag_fold = fold(q), fold(tag)
    if scope in ("nacional", "regional"):
        cc = "CL" if scope == "nacional" else country
        stations, stale = country_stations(cc)
        rows = stations
        if scope == "nacional" and region:
            rows = [s for s in rows if s["region"] == region] if region != "otras" else [s for s in rows if not s["region"]]
        if genre:
            rows = [s for s in rows if station_matches_genre(s, genre)]
        if tag_fold:
            rows = [s for s in rows if any(tag_fold in t for t in s["_tagfold"])]
        if q_fold:
            rows = [s for s in rows if q_fold in s["_namefold"] or any(q_fold == t for t in s["_tagfold"])
                    or q_fold in fold(s["state"])]
        rows = sorted(rows, key=_sort_key(order))
        page = rows[offset: offset + limit]
        return {"stations": page, "total": len(rows), "has_more": offset + limit < len(rows), "stale": stale,
                "next_offset": offset + limit}

    field, reverse = ORDERS[order]
    params = {"hidebroken": "true", "is_https": "true", "order": field, "reverse": "true" if reverse else "false",
              "offset": str(offset), "limit": str(limit + 1)}
    if q_fold:
        params["name"] = q.strip()[:80]
    if genre:
        params["tag"] = GENRE_BY_KEY[genre][2]
    elif tag_fold:
        params["tag"] = tag.strip()[:40]
    if country:
        params["countrycode"] = country
    key = ("search",) + tuple(sorted(params.items()))
    rows, stale = cache.fetch(key, lambda: _search_raw(params, limit))
    raw_count, stations = rows
    if stale:
        _remember(stations)
    # Se pide limit+1 para saber si hay más. Las emisoras descartadas (HLS, sin https) hacen que
    # una página pueda traer menos de «limit»: el navegador sigue desde next_offset.
    return {"stations": stations, "total": None, "has_more": raw_count > limit, "stale": stale,
            "next_offset": offset + limit}


def _search_raw(params, limit):
    rows = _request("/json/stations/search", params)
    rows = rows if isinstance(rows, list) else []
    return len(rows), _normalize_all(rows[:limit])


def countries():
    def load():
        rows = _request("/json/countries", {"hidebroken": "true", "order": "stationcount", "reverse": "true"})
        merged = {}
        for row in rows if isinstance(rows, list) else []:
            code = str(row.get("iso_3166_1") or row.get("countrycode") or "").upper()
            if CC_RE.match(code) and _int(row.get("stationcount")) > 0:
                merged[code] = merged.get(code, 0) + _int(row.get("stationcount"))
                merged.setdefault("_name_" + code, _clean(row.get("name"), 80))
        out = [{"code": code, "name": merged.get("_name_" + code, code), "stations": count}
               for code, count in merged.items() if not code.startswith("_name_")]
        out.sort(key=lambda c: -c["stations"])
        return out
    return cache.fetch(("countries",), load)


def top_tags(limit=40):
    def load():
        rows = _request("/json/tags", {"hidebroken": "true", "order": "stationcount", "reverse": "true",
                                       "limit": str(limit)})
        return [{"name": _clean(r.get("name"), 40), "stations": _int(r.get("stationcount"))}
                for r in rows if isinstance(r, dict) and _clean(r.get("name"), 40)] if isinstance(rows, list) else []
    return cache.fetch(("tags", limit), load)


def meta():
    """Países, regiones de Chile con cantidades, géneros y etiquetas. Tolera fallas parciales."""
    out = {"regions": [], "latam": LATAM, "countries": [], "genres": [], "tags": [], "tags_cl": [],
           "stale": False, "partial": False}
    cl = None
    try:
        cl, stale = country_stations("CL")
        out["stale"] |= stale
    except RadioError:
        out["partial"] = True
    counts = {}
    for station in cl or []:
        counts[station["region"] or "otras"] = counts.get(station["region"] or "otras", 0) + 1
    out["regions"] = [{"key": key, "label": label, "iso": iso, "stations": counts.get(key, 0) if cl is not None else None}
                      for key, label, iso, _ in CL_REGIONS]
    out["cl_total"] = len(cl) if cl is not None else None
    out["cl_without_region"] = counts.get("otras", 0) if cl is not None else None
    out["genres"] = [{"key": key, "label": label,
                      "stations_cl": sum(1 for s in cl if station_matches_genre(s, key)) if cl is not None else None}
                     for key, label, _, _ in GENRES]
    if cl:
        tally = {}
        for station in cl:
            for tag in station["tags"]:
                tally[tag.lower()] = tally.get(tag.lower(), 0) + 1
        out["tags_cl"] = [{"name": name, "stations": n} for name, n in sorted(tally.items(), key=lambda t: -t[1])[:30]]
    try:
        rows, stale = countries()
        out["countries"] = rows
        out["stale"] |= stale
    except RadioError:
        out["partial"] = True
    try:
        rows, stale = top_tags()
        out["tags"] = rows
        out["stale"] |= stale
    except RadioError:
        out["partial"] = True
    if cl is None and not out["countries"] and not out["tags"]:
        raise RadioError(UNAVAILABLE)
    return out


def lookup(uuid):
    """Emisora por uuid: primero lo ya visto en búsquedas, si no Radio Browser (byuuid, con caché)."""
    with _seen_lock:
        station = _seen.get(uuid)
    if station:
        return station, None
    def load():
        rows = _request("/json/stations/byuuid", {"uuids": uuid})
        return rows if isinstance(rows, list) else []
    rows, _ = cache.fetch(("uuid", uuid), load, ttl=TTL)
    raw = next((r for r in rows if isinstance(r, dict) and str(r.get("stationuuid", "")).lower() == uuid), None)
    if raw is None:
        return None, "not_found"
    station = normalize_station(raw)
    if station is None:
        return None, "unplayable"
    _remember([station])
    return station, None


# ---------------------------------------------------------------- clics (en segundo plano)
_clicks = ThreadPoolExecutor(max_workers=1, thread_name_prefix="radio-click")
_click_lock = threading.Lock()
_click_seen = OrderedDict()
CLICK_DEDUP = 3600


def register_click(user_id, uuid):
    """Avisa a Radio Browser que se escuchó la emisora (cuenta 1 por IP al día). No bloquea la respuesta."""
    now = time.time()
    with _click_lock:
        last = _click_seen.get((user_id, uuid))
        if last and now - last < CLICK_DEDUP:
            return False
        _click_seen[(user_id, uuid)] = now
        _click_seen.move_to_end((user_id, uuid))
        while len(_click_seen) > 5000:
            _click_seen.popitem(last=False)
    _clicks.submit(_click, uuid)
    return True


def _click(uuid):
    try:
        _request(f"/json/url/{uuid}", read_json=False)
    except Exception as error:  # noqa: BLE001  (un clic perdido no importa)
        log.info("clic de radio no registrado: %s", type(error).__name__)


# ---------------------------------------------------------------- favoritos
def favorite_ids(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT stationuuid FROM radio_favorites WHERE user_id = %s", (user_id,))
        return {row["stationuuid"] for row in cur.fetchall()}


def list_favorites(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stationuuid, name, url, favicon, country, countrycode, tags, codec, bitrate, created_at "
            "FROM radio_favorites WHERE user_id = %s ORDER BY created_at DESC, id DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    out = []
    for row in rows:
        tags = [t for t in (row["tags"] or "").split(",") if t]
        out.append({
            "stationuuid": row["stationuuid"], "name": row["name"], "url": row["url"], "favicon": row["favicon"] or "",
            "country": row["country"] or "", "countrycode": row["countrycode"] or "", "tags": tags,
            "codec": row["codec"] or "", "bitrate": row["bitrate"] or 0, "favorite": True,
            "state": "", "region": "", "region_label": "", "homepage": "",
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
    return out


def add_favorite(conn, user_id, station):
    """Guarda la emisora. Devuelve True si es nueva, False si ya estaba. RadioError(409) al pasar el límite."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM radio_favorites WHERE user_id = %s AND stationuuid = %s",
                    (user_id, station["stationuuid"]))
        if cur.fetchone():
            return False
        cur.execute("SELECT COUNT(*) AS n FROM radio_favorites WHERE user_id = %s", (user_id,))
        if cur.fetchone()["n"] >= MAX_FAVORITES:
            raise RadioError(f"Puedes guardar hasta {MAX_FAVORITES} radios.", 409)
        cur.execute(
            "INSERT IGNORE INTO radio_favorites (user_id, stationuuid, name, url, favicon, country, countrycode, "
            "tags, codec, bitrate) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, station["stationuuid"], station["name"][:160], station["url"][:1000],
             station["favicon"][:1000], station["country"][:80], station["countrycode"][:2],
             ",".join(station["tags"])[:400], station["codec"][:20], station["bitrate"]),
        )
        return cur.rowcount == 1


def remove_favorite(conn, user_id, uuid):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM radio_favorites WHERE user_id = %s AND stationuuid = %s", (user_id, uuid))
        return cur.rowcount
