"""Géneros de la biblioteca: normaliza las etiquetas de las canciones y arma el índice por género.

Reglas:
- Se separan las etiquetas con varios géneros («Rock; Pop», «Hard Rock / Blues», «Rock, Metal»).
- Se traducen los códigos numéricos de ID3 («(17)», «17», «(17)Rock») a su nombre.
- Se agrupan sin importar mayúsculas, tildes, espacios o guiones («hip-hop» = «Hip Hop»),
  con algunos alias conocidos («Trash» -> «Thrash Metal», «RnB» -> «R&B»).
- Las canciones sin etiqueta (o «Unknown», «Sin género»…) van a «Sin género», al final.
El índice se guarda en memoria y se recalcula solo cuando cambian las canciones (firma de la tabla).
"""

import hashlib
import re
import threading
import unicodedata

try:
    from mutagen.id3 import TCON

    ID3_GENRES = list(TCON.GENRES)
except Exception:  # pragma: no cover - mutagen siempre está en requirements
    ID3_GENRES = ["Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal"]

NO_GENRE = "Sin género"
NO_GENRE_SLUG = "sin-genero"
MAX_NAME = 60

# Valores que en la práctica significan «sin género».
EMPTY_KEYS = {
    "", "sin genero", "unknown", "unknown genre", "desconocido", "genre", "genero", "none", "null",
    "n a", "na", "misc", "varios", "various", "default", "undefined", "sin clasificar",
}

# Alias frecuentes (clave normalizada -> nombre canónico).
ALIASES = {
    "trash": "Thrash Metal",
    "trash metal": "Thrash Metal",
    "thrash": "Thrash Metal",
    "heavy": "Heavy Metal",
    "prog rock": "Progressive Rock",
    "prog metal": "Progressive Metal",
    "hiphop": "Hip Hop",
    "hip hop": "Hip Hop",
    "rap hip hop": "Hip Hop",
    "rnb": "R&B",
    "r b": "R&B",
    "r&b": "R&B",
    "rhythm and blues": "R&B",
    "rock and roll": "Rock & Roll",
    "rock n roll": "Rock & Roll",
    "rock & roll": "Rock & Roll",
    "electronica": "Electrónica",
    "electronic": "Electrónica",
    "electro": "Electrónica",
    "edm": "EDM",
    "latin": "Latino",
    "latina": "Latino",
    "musica latina": "Latino",
    "clasica": "Clásica",
    "classical": "Clásica",
    "soundtrack": "Banda sonora",
    "banda sonora": "Banda sonora",
    "alternative": "Alternativa",
    "alternativo": "Alternativa",
    "indie rock": "Indie Rock",
    "k pop": "K-Pop",
    "kpop": "K-Pop",
    "lofi": "Lo-fi",
    "lo fi": "Lo-fi",
    "other": "Otros",
    "otro": "Otros",
    "otros": "Otros",
}

# Paleta (oscuro, brillante) para tarjetas/cabeceras con texto claro (WCAG AA).
# Se mantiene en tema claro: el degradado oscuro del sheet da contraste al texto blanco.
PALETTE = {
    "crimson": ("#3b0d18", "#ff4d6d"),
    "red": ("#2e0b0b", "#e5484d"),
    "orange": ("#3a1a06", "#ff8a3d"),
    "gold": ("#2f2707", "#e8c547"),
    "green": ("#10331a", "#5fd35b"),
    "aqua": ("#0b3325", "#2ef2c0"),
    "cyan": ("#062f33", "#3fd6e0"),
    "blue": ("#0c2a3f", "#3fa9ff"),
    "violet": ("#1d1446", "#8d7bff"),
    "magenta": ("#3a0f3a", "#ff5fd2"),
    "pink": ("#34101f", "#ff7aa8"),
    "brown": ("#2a1c14", "#c98b5a"),
    "steel": ("#1b2530", "#8fa6bd"),
    # Colores de reserva: solo se usan cuando el color preferido ya esta tomado (nunca por el hash).
    "lime": ("#1f3005", "#b6e63c"),
    "sand": ("#2e2a1f", "#d8c9a3"),
    "coral": ("#3a1612", "#ff7b6b"),
    "indigo": ("#14173f", "#6475f5"),
    "teal": ("#0a2f2c", "#14b8a6"),
}
HASH_ORDER = ["crimson", "orange", "gold", "green", "aqua", "cyan", "blue", "violet", "magenta", "pink", "brown", "steel", "red"]
EXTRA_ORDER = ["lime", "sand", "coral", "indigo", "teal"]
ALL_COLORS = HASH_ORDER + EXTRA_ORDER
# Colores fijos para géneros comunes (así no cambian ni se repiten entre los más usados).
GENRE_COLORS = {
    "rock": "crimson", "metal": "steel", "heavy metal": "orange", "hard rock": "gold",
    "thrash metal": "red", "progressive metal": "violet", "progressive rock": "cyan",
    "death metal": "brown", "black metal": "steel", "punk": "green", "grunge": "brown",
    "alternativa": "aqua", "indie rock": "cyan", "pop": "pink", "k pop": "magenta",
    "hip hop": "orange", "rap": "gold", "r&b": "magenta", "electronica": "magenta",
    "edm": "blue", "dance": "blue", "techno": "violet", "house": "cyan", "ambient": "steel",
    "jazz": "violet", "blues": "blue", "soul": "brown", "funk": "gold", "reggae": "green",
    "latino": "gold", "reggaeton": "orange", "salsa": "crimson", "cumbia": "green",
    "clasica": "brown", "banda sonora": "steel", "country": "brown", "folk": "green",
    "rock & roll": "red", "dream pop": "aqua", "ritmo latino": "orange", "lo fi": "violet",
    # Categorias de la biblioteca ordenada (D:\Biblioteca_SpotyOFO): un color fijo cada una, todos distintos.
    "reggaeton y urbano latino": "orange", "electronica y dance": "cyan", "rock en espanol": "crimson",
    "nueva ola y clasicos chilenos": "brown", "retro 70s y 80s": "gold", "rock instrumental": "violet",
    "pop y baladas": "pink", "rock internacional": "red", "hip hop y rap": "aqua", "disco": "magenta",
}
LIBRARY_CATEGORIES = {
    "reggaeton y urbano latino", "electronica y dance", "rock en espanol", "nueva ola y clasicos chilenos",
    "retro 70s y 80s", "rock instrumental", "pop y baladas", "rock internacional", "hip hop y rap", "disco", "metal",
}
SMALL_WORDS = {"de", "del", "la", "el", "y", "and", "n", "of", "the"}
SPLIT_RE = re.compile(r"\s*(?:;|,|/|\||\\|\x00)\s*")
ID3_CODE_RE = re.compile(r"\((\d{1,3})\)|^(\d{1,3})$")
SLUG_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fold(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def genre_key(name):
    """Clave para agrupar: sin tildes, en minúsculas, con espacios/guiones/guion bajo unificados."""
    key = fold(name).casefold().replace("'", "")
    key = re.sub(r"[\s_\-\.]+", " ", key).strip()
    return key


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", fold(name).casefold().replace("&", " and ")).strip("-")
    return slug[:80] or "genero"


def _expand_id3(part):
    """«(17)» / «17» / «(17)Rock» -> nombres. Devuelve lista."""
    out = []
    rest = part
    for match in ID3_CODE_RE.finditer(part):
        code = int(match.group(1) or match.group(2))
        if 0 <= code < len(ID3_GENRES):
            out.append(ID3_GENRES[code])
        rest = rest.replace(match.group(0), " ")
    rest = re.sub(r"\((?:RX|CR)\)", " ", rest, flags=re.I).strip()  # remix / cover
    if rest:
        out.append(rest)
    return out


def split_genres(raw):
    """Etiqueta cruda -> lista de nombres (sin normalizar mayúsculas), sin repetidos."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return []
    names = []
    for part in SPLIT_RE.split(text):
        part = part.strip(" \t-–·")
        if not part:
            continue
        for name in _expand_id3(part):
            name = name.strip()[:MAX_NAME]
            if name and genre_key(name) not in EMPTY_KEYS:
                names.append(name)
    seen = set()
    unique = []
    for name in names:
        key = genre_key(ALIASES.get(genre_key(name), name))
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def pretty(name):
    """Nombre para mostrar a partir de la escritura más usada."""
    alias = ALIASES.get(genre_key(name))
    if alias:
        return alias
    letters = [ch for ch in name if ch.isalpha()]
    if letters and (all(ch.islower() for ch in letters) or (all(ch.isupper() for ch in letters) and len(letters) > 4)):
        words = name.lower().split(" ")
        return " ".join(
            word if (index and word in SMALL_WORDS) else word[:1].upper() + word[1:]
            for index, word in enumerate(words)
        )
    return name


NO_GENRE_COLORS = ("#1d2027", "#6b7280")


def preferred_color(slug, name=""):
    """Color preferido: fijo para géneros comunes; si no, derivado del slug (siempre el mismo)."""
    fixed = GENRE_COLORS.get(genre_key(name))
    if fixed:
        return fixed
    digest = hashlib.sha1(slug.encode("utf-8")).digest()
    return HASH_ORDER[digest[0] % len(HASH_ORDER)]


def colors(slug, name=""):
    if slug == NO_GENRE_SLUG:
        return NO_GENRE_COLORS
    return PALETTE[preferred_color(slug, name)]


def assign_colors(items):
    """Pinta sin repetir colores mientras haya libres (18 colores). Orden de prioridad (estable, por ranking):
    1) las 11 categorias de la biblioteca, que siempre conservan su color fijo; 2) otros generos con color fijo;
    3) el resto, que toma su color preferido o el siguiente libre de la paleta.
    Solo se repiten colores cuando hay mas generos que colores."""
    used = set()

    def tier(item):
        key = genre_key(item["name"])
        if key in LIBRARY_CATEGORIES:
            return 0
        return 1 if key in GENRE_COLORS else 2

    ordered = []
    for item in items:
        if item["slug"] == NO_GENRE_SLUG:
            item["accent_a"], item["accent_b"] = NO_GENRE_COLORS
        else:
            ordered.append(item)
    for item in sorted(ordered, key=tier):
        choice = preferred_color(item["slug"], item["name"])
        if choice in used and len(used) < len(ALL_COLORS):
            start = ALL_COLORS.index(choice)
            for step in range(1, len(ALL_COLORS)):
                candidate = ALL_COLORS[(start + step) % len(ALL_COLORS)]
                if candidate not in used:
                    choice = candidate
                    break
        used.add(choice)
        item["accent_a"], item["accent_b"] = PALETTE[choice]


def build_index(rows):
    """rows: dicts con id, genre, duration_ms, artist_id. Devuelve {slug: género} ordenado."""
    buckets = {}
    spellings = {}
    for row in rows:
        names = split_genres(row.get("genre"))
        keys = []
        for name in names:
            canonical = ALIASES.get(genre_key(name))
            key = genre_key(canonical or name)
            spellings.setdefault(key, {}).setdefault(canonical or name, 0)
            spellings[key][canonical or name] += 1
            keys.append(key)
        if not keys:
            keys = [None]
        for key in keys:
            bucket = buckets.setdefault(key, {"track_ids": [], "duration_ms": 0, "artists": set()})
            bucket["track_ids"].append(row["id"])
            bucket["duration_ms"] += int(row.get("duration_ms") or 0)
            if row.get("artist_id") is not None:
                bucket["artists"].add(row["artist_id"])
    items = []
    used = set()
    for key, bucket in buckets.items():
        if key is None:
            name, slug = NO_GENRE, NO_GENRE_SLUG
        else:
            options = spellings[key]
            best = sorted(options.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
            name = pretty(best)
            slug = slugify(name)
            if slug == NO_GENRE_SLUG:
                slug = "genero-" + slug
        base, n = slug, 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        items.append({
            "slug": slug,
            "name": name,
            "count": len(bucket["track_ids"]),
            "duration_ms": bucket["duration_ms"],
            "artists": len(bucket["artists"]),
            "accent_a": "",
            "accent_b": "",
            "track_ids": bucket["track_ids"],
        })
    items.sort(key=lambda item: (item["slug"] == NO_GENRE_SLUG, -item["count"], genre_key(item["name"])))
    assign_colors(items)
    return {item["slug"]: item for item in items}


_cache = {"signature": None, "index": {}}
_lock = threading.Lock()

SIGNATURE_SQL = """
SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id,
       COALESCE(BIT_XOR(CRC32(CONCAT_WS(':', id, genre, duration_ms, artist_id))), 0) AS sig
FROM tracks
"""


def get_index(conn):
    """Índice de géneros, recalculado solo si cambió la tabla de canciones."""
    with conn.cursor() as cur:
        cur.execute(SIGNATURE_SQL)
        row = cur.fetchone()
        signature = (int(row["n"]), int(row["max_id"]), int(row["sig"]))
        if _cache["signature"] == signature:
            return _cache["index"]
        cur.execute("SELECT id, genre, duration_ms, artist_id FROM tracks ORDER BY id")
        rows = cur.fetchall()
    index = build_index(rows)
    with _lock:
        _cache["signature"] = signature
        _cache["index"] = index
    return index


def public(item):
    return {key: value for key, value in item.items() if key != "track_ids"}


def valid_slug(slug):
    return bool(slug) and len(slug) <= 90 and bool(SLUG_OK.match(slug))
