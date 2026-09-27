"""Subida de archivos propios: validación, etiquetas, portada y alta en MariaDB."""

import base64
import hashlib
import html
import os
import re
import secrets
import unicodedata
import zlib
from pathlib import Path

import mutagen
from mutagen.aac import AAC
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

ROOT = Path(__file__).resolve().parent
AUDIO_ROOT = ROOT / "static" / "audio"
COVER_ROOT = ROOT / "static" / "covers"
USER_AUDIO_DIR = AUDIO_ROOT / "user"
USER_COVER_DIR = COVER_ROOT / "user"
TMP_DIR = USER_AUDIO_DIR / ".tmp"
PENDING_FILE = USER_AUDIO_DIR / ".pending-delete.txt"

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_COVER_BYTES = 12 * 1024 * 1024
CHUNK = 1024 * 1024
ACCEPTED_EXT = {".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav"}
FORMATS = [
    (MP3, "mp3"),
    (MP4, "m4a"),
    (AAC, "aac"),
    (FLAC, "flac"),
    (OggVorbis, "ogg"),
    (OggOpus, "opus"),
    (WAVE, "wav"),
]
PALETTE = [
    ("#12343c", "#7dffe0"),
    ("#241018", "#ff6d93"),
    ("#1a1430", "#b7a6ff"),
    ("#3a2410", "#ffc56d"),
    ("#241612", "#ff7358"),
    ("#10243a", "#8fd4ff"),
]


class UploadError(Exception):
    def __init__(self, message, code=400, **extra):
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra


def ensure_dirs():
    for folder in (USER_AUDIO_DIR, USER_COVER_DIR, TMP_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# ---------- texto ----------

def clean_text(value, limit):
    if value is None:
        return ""
    text = str(value)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def slugify(value, limit=40):
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:limit].strip("-")) or "pista"


def original_filename(name):
    base = str(name or "").replace("\\", "/").split("/")[-1]
    return clean_text(base, 255) or "archivo"


def parse_int(value, low, high):
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", str(value))
    if not match:
        return None
    number = int(match.group(1))
    return max(low, min(high, number))


def pick_colors(label):
    return PALETTE[zlib.crc32(label.lower().encode("utf-8")) % len(PALETTE)]


# ---------- etiquetas ----------

def _first(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _id3_text(tags, key):
    frame = tags.get(key) if tags is not None else None
    if frame is None:
        return None
    if key == "TCON" and getattr(frame, "genres", None):
        return frame.genres[0]
    text = getattr(frame, "text", None)
    return str(_first(text)) if text else None


def _vorbis(tags, *keys):
    if tags is None:
        return None
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            value = None
        value = _first(value)
        if value:
            return str(value)
    return None


def _image_ext(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


RIFF_INFO = {
    b"INAM": "title",
    b"IART": "artist",
    b"IPRD": "album",
    b"IPRT": "track",
    b"ITRK": "track",
    b"IGNR": "genre",
    b"ICRD": "date",
}


def read_riff_info(path):
    """Etiquetas LIST/INFO de WAV (las que escriben muchos programas en vez de ID3)."""
    found = {}
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return found
            while True:
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                cid, size = chunk[:4], int.from_bytes(chunk[4:], "little")
                if cid == b"LIST" and size <= 1024 * 1024:
                    data = handle.read(size)
                    if data[:4] == b"INFO":
                        pos = 4
                        while pos + 8 <= len(data):
                            sub, sub_size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
                            raw = data[pos + 8:pos + 8 + sub_size].split(b"\x00", 1)[0]
                            key = RIFF_INFO.get(sub)
                            if key and raw and key not in found:
                                try:
                                    found[key] = raw.decode("utf-8")
                                except UnicodeDecodeError:
                                    found[key] = raw.decode("latin-1")
                            pos += 8 + sub_size + (sub_size & 1)
                else:
                    handle.seek(size, 1)
                if size & 1:
                    handle.seek(1, 1)
    except OSError:
        return found
    return found


def read_metadata(audio, kind):
    """Devuelve dict con title, artist, albumartist, album, track, genre, date, bpm, key, cover."""
    tags = audio.tags
    meta = {}
    cover = None
    if kind in ("mp3", "wav"):
        meta = {
            "title": _id3_text(tags, "TIT2"),
            "artist": _id3_text(tags, "TPE1"),
            "albumartist": _id3_text(tags, "TPE2"),
            "album": _id3_text(tags, "TALB"),
            "track": _id3_text(tags, "TRCK"),
            "genre": _id3_text(tags, "TCON"),
            "date": _id3_text(tags, "TDRC") or _id3_text(tags, "TYER"),
            "bpm": _id3_text(tags, "TBPM"),
            "key": _id3_text(tags, "TKEY"),
        }
        if tags is not None:
            pictures = tags.getall("APIC") if hasattr(tags, "getall") else []
            front = [pic for pic in pictures if getattr(pic, "type", None) == 3]
            chosen = (front or pictures or [None])[0]
            if chosen is not None:
                cover = chosen.data
    elif kind == "m4a":
        track = _first(tags.get("trkn")) if tags is not None else None
        tempo = _first(tags.get("tmpo")) if tags is not None else None
        meta = {
            "title": _vorbis(tags, "\xa9nam"),
            "artist": _vorbis(tags, "\xa9ART"),
            "albumartist": _vorbis(tags, "aART"),
            "album": _vorbis(tags, "\xa9alb"),
            "track": track[0] if isinstance(track, tuple) else track,
            "genre": _vorbis(tags, "\xa9gen"),
            "date": _vorbis(tags, "\xa9day"),
            "bpm": tempo,
            "key": None,
        }
        covers = tags.get("covr") if tags is not None else None
        if covers:
            cover = bytes(covers[0])
    elif kind in ("flac", "ogg", "opus"):
        meta = {
            "title": _vorbis(tags, "title"),
            "artist": _vorbis(tags, "artist"),
            "albumartist": _vorbis(tags, "albumartist", "album artist"),
            "album": _vorbis(tags, "album"),
            "track": _vorbis(tags, "tracknumber"),
            "genre": _vorbis(tags, "genre"),
            "date": _vorbis(tags, "date", "year"),
            "bpm": _vorbis(tags, "bpm"),
            "key": _vorbis(tags, "initialkey", "key"),
        }
        if kind == "flac" and audio.pictures:
            front = [pic for pic in audio.pictures if pic.type == 3]
            cover = (front or audio.pictures)[0].data
        elif tags is not None:
            raw = _vorbis(tags, "metadata_block_picture")
            if raw:
                try:
                    cover = Picture(base64.b64decode(raw)).data
                except Exception:
                    cover = None
            if cover is None:
                legacy = _vorbis(tags, "coverart")
                if legacy:
                    try:
                        cover = base64.b64decode(legacy)
                    except Exception:
                        cover = None
    if kind == "wav" and not meta.get("title") and getattr(audio, "filename", None):
        for key, value in read_riff_info(audio.filename).items():
            if not meta.get(key):
                meta[key] = value
    meta["cover"] = cover if cover and len(cover) <= MAX_COVER_BYTES and _image_ext(cover) else None
    return meta


# ---------- archivos ----------

def inside(path, folder):
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def remove_file(path, folder):
    """Borra un archivo dentro de folder. Si Windows lo tiene abierto, lo deja pendiente."""
    if not inside(path, folder) or not path.exists():
        return True
    try:
        path.unlink()
        return True
    except OSError:
        with PENDING_FILE.open("a", encoding="utf-8") as handle:
            handle.write(str(path.resolve()) + "\n")
        return False


def purge_pending():
    if not PENDING_FILE.exists():
        return
    try:
        lines = PENDING_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    left = []
    for line in lines:
        path = Path(line.strip())
        if not line.strip() or not path.exists():
            continue
        if not (inside(path, USER_AUDIO_DIR) or inside(path, USER_COVER_DIR)):
            continue
        try:
            path.unlink()
        except OSError:
            left.append(str(path))
    try:
        if left:
            PENDING_FILE.write_text("\n".join(left) + "\n", encoding="utf-8")
        else:
            PENDING_FILE.unlink()
    except OSError:
        pass
    if TMP_DIR.exists():
        for stale in TMP_DIR.glob("*.part"):
            try:
                stale.unlink()
            except OSError:
                pass


def write_initial_cover(relative, c1, c2, label):
    initial = html.escape((label[:1] or "S").upper())
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="640" height="640" fill="url(#bg)"/>
  <circle cx="470" cy="170" r="96" fill="#ffffff" fill-opacity="0.16"/>
  <path d="M0 520 C160 440 300 600 640 470 L640 640 L0 640 Z" fill="#000000" fill-opacity="0.22"/>
  <text x="72" y="430" fill="#ffffff" font-family="Segoe UI, sans-serif" font-size="280" font-weight="700">{initial}</text>
</svg>
"""
    path = COVER_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def save_cover(data):
    ext = _image_ext(data)
    name = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
    path = USER_COVER_DIR / name
    if not path.exists():
        path.write_bytes(data)
    return f"user/{name}"


def cover_in_use(cur, relative):
    cur.execute(
        """
        SELECT (SELECT COUNT(*) FROM albums WHERE cover_file = %s)
             + (SELECT COUNT(*) FROM artists WHERE cover_file = %s)
             + (SELECT COUNT(*) FROM playlists WHERE cover_file = %s) AS n
        """,
        (relative, relative, relative),
    )
    return cur.fetchone()["n"] > 0


def detect(path):
    try:
        audio = mutagen.File(path)
    except Exception:
        audio = None
    if audio is None:
        raise UploadError("Formato no reconocido. Usa MP3, M4A/AAC, FLAC, OGG u WAV.", 415)
    for cls, kind in FORMATS:
        if isinstance(audio, cls):
            break
    else:
        raise UploadError("Formato no admitido. Usa MP3, M4A/AAC, FLAC, OGG u WAV.", 415)
    length = float(getattr(audio.info, "length", 0) or 0)
    if length <= 0:
        raise UploadError("No se pudo leer la duración del archivo.", 415)
    return audio, kind, length


# ---------- alta ----------

def store_upload(conn, storage):
    """Guarda un FileStorage de Flask. Devuelve el id de la pista nueva."""
    ensure_dirs()
    original = original_filename(storage.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext and ext not in ACCEPTED_EXT:
        raise UploadError("Extensión no admitida. Usa MP3, M4A/AAC, FLAC, OGG u WAV.", 415)
    tmp = TMP_DIR / f"{secrets.token_hex(8)}.part"
    digest = hashlib.sha256()
    size = 0
    final = None
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = storage.stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise UploadError("El archivo supera 200 MB.", 413)
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadError("El archivo está vacío.")
        file_hash = digest.hexdigest()
        with conn.cursor() as cur:
            cur.execute("SELECT id, title FROM tracks WHERE file_hash = %s", (file_hash,))
            dup = cur.fetchone()
        if dup:
            raise UploadError(f"Ya estaba en tu biblioteca: «{dup['title']}».", 409, track_id=dup["id"])

        audio, kind, length = detect(tmp)
        meta = read_metadata(audio, kind)
        del audio

        stem = os.path.splitext(original)[0]
        title = clean_text(meta.get("title"), 160)
        artist = clean_text(meta.get("artist"), 120)
        if not title:
            guess = [part.strip() for part in stem.split(" - ", 1)]
            if len(guess) == 2 and not artist:
                artist, title = clean_text(guess[0], 120), clean_text(guess[1], 160)
            else:
                title = clean_text(stem.replace("_", " "), 160)
        title = title or "Sin título"
        artist = artist or "Artista desconocido"
        album_artist = clean_text(meta.get("albumartist"), 120) or artist
        album = clean_text(meta.get("album"), 160) or "Sin álbum"
        genre = clean_text(meta.get("genre"), 80) or "Sin género"
        year = parse_int(meta.get("date"), 0, 9999) or 0
        if year and year < 1000:
            year = 0
        bpm = 0
        try:
            bpm = max(0, min(999, int(round(float(str(meta.get("bpm") or 0).replace(",", "."))))))
        except (TypeError, ValueError):
            bpm = 0
        musical_key = clean_text(meta.get("key"), 32)
        track_number = parse_int(meta.get("track"), 0, 32767)
        duration_ms = int(round(length * 1000))

        final_name = f"{file_hash[:16]}.{kind}"
        final = USER_AUDIO_DIR / final_name
        os.replace(tmp, final)

        conn.begin()
        try:
            with conn.cursor() as cur:
                artist_id = find_or_create_artist(cur, album_artist, genre)
                album_id, album_cover = find_or_create_album(cur, artist_id, album, year)
                if meta.get("cover") and album_cover.endswith(".svg"):
                    new_cover = save_cover(meta["cover"])
                    cur.execute("UPDATE albums SET cover_file = %s WHERE id = %s", (new_cover, album_id))
                    if album_cover.startswith("user/") and not cover_in_use(cur, album_cover):
                        remove_file(COVER_ROOT / album_cover, USER_COVER_DIR)
                if album_artist.lower() != artist.lower():
                    track_artist_id = find_or_create_artist(cur, artist, genre)
                else:
                    track_artist_id = artist_id
                if track_number is None:
                    cur.execute(
                        "SELECT COALESCE(MAX(track_number), 0) + 1 AS n FROM tracks WHERE album_id = %s",
                        (album_id,),
                    )
                    track_number = min(32767, cur.fetchone()["n"])
                cur.execute(
                    """
                    INSERT INTO tracks (
                      slug, album_id, artist_id, title, track_number, duration_ms, audio_file,
                      genre, bpm, musical_key, source, file_hash, original_name, file_size, uploaded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'user', %s, %s, %s, NOW())
                    """,
                    (
                        f"u-{file_hash[:12]}",
                        album_id,
                        track_artist_id,
                        title,
                        track_number,
                        duration_ms,
                        f"user/{final_name}",
                        genre,
                        bpm,
                        musical_key,
                        file_hash,
                        original,
                        size,
                    ),
                )
                track_id = cur.lastrowid
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if exc.args and exc.args[0] == 1062:
                # Otra subida simultánea del mismo archivo ganó: el archivo final es idéntico y le pertenece.
                final = None
                raise UploadError("Ya estaba en tu biblioteca.", 409) from exc
            raise
        final = None
        return track_id
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        if final is not None and final.exists():
            remove_file(final, USER_AUDIO_DIR)


def unique_slug(cur, table, label):
    while True:
        slug = f"u-{slugify(label)}-{secrets.token_hex(3)}"
        cur.execute(f"SELECT id FROM {table} WHERE slug = %s", (slug,))
        if not cur.fetchone():
            return slug


def find_or_create_artist(cur, name, genre):
    cur.execute("SELECT id FROM artists WHERE source = 'user' AND name = %s LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    accent_a, accent_b = pick_colors(name)
    cur.execute(
        """
        INSERT INTO artists (slug, name, genre, origin, bio, accent_a, accent_b, cover_file, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user')
        """,
        (
            unique_slug(cur, "artists", name),
            name,
            genre,
            "Tus archivos",
            "Música que subiste a SpotyOFO desde tus archivos.",
            accent_a,
            accent_b,
            "pending.svg",
        ),
    )
    artist_id = cur.lastrowid
    cover = f"user/artist-{artist_id}.svg"
    write_initial_cover(cover, accent_a, accent_b, name)
    cur.execute("UPDATE artists SET cover_file = %s WHERE id = %s", (cover, artist_id))
    return artist_id


def find_or_create_album(cur, artist_id, title, year):
    cur.execute(
        "SELECT id, cover_file, year FROM albums WHERE source = 'user' AND artist_id = %s AND title = %s LIMIT 1",
        (artist_id, title),
    )
    row = cur.fetchone()
    if row:
        if not row["year"] and year:
            cur.execute("UPDATE albums SET year = %s WHERE id = %s", (year, row["id"]))
        return row["id"], row["cover_file"]
    accent_a, accent_b = pick_colors(title)
    cur.execute(
        """
        INSERT INTO albums (slug, artist_id, title, year, credit, accent_a, accent_b, cover_file, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user')
        """,
        (unique_slug(cur, "albums", title), artist_id, title, year, "Subido desde tus archivos", accent_a, accent_b, "pending.svg"),
    )
    album_id = cur.lastrowid
    cover = f"user/album-{album_id}.svg"
    write_initial_cover(cover, accent_a, accent_b, title)
    cur.execute("UPDATE albums SET cover_file = %s WHERE id = %s", (cover, album_id))
    return album_id, cover


# ---------- baja ----------

def delete_upload(conn, track_id):
    """Elimina una pista subida, su archivo y el álbum/artista si quedan vacíos."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, audio_file, album_id, artist_id FROM tracks WHERE id = %s", (track_id,))
        track = cur.fetchone()
    if not track:
        raise UploadError("Pista no encontrada.", 404)
    if track["source"] != "user":
        raise UploadError("Solo se pueden eliminar canciones que subiste tú.", 403)
    covers = []
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tracks WHERE id = %s", (track_id,))
            cur.execute("SELECT id, cover_file, artist_id FROM albums WHERE id = %s", (track["album_id"],))
            album = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE album_id = %s", (track["album_id"],))
            if album and cur.fetchone()["n"] == 0:
                cur.execute("DELETE FROM albums WHERE id = %s AND source = 'user'", (album["id"],))
                covers.append(album["cover_file"])
            for artist_id in {track["artist_id"], album["artist_id"] if album else track["artist_id"]}:
                cur.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM tracks WHERE artist_id = %s)
                         + (SELECT COUNT(*) FROM albums WHERE artist_id = %s) AS n
                    """,
                    (artist_id, artist_id),
                )
                if cur.fetchone()["n"] == 0:
                    cur.execute("SELECT cover_file FROM artists WHERE id = %s AND source = 'user'", (artist_id,))
                    artist = cur.fetchone()
                    if artist:
                        cur.execute("DELETE FROM artists WHERE id = %s", (artist_id,))
                        covers.append(artist["cover_file"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    audio_rel = track["audio_file"]
    if audio_rel.startswith("user/"):
        remove_file(AUDIO_ROOT / audio_rel, USER_AUDIO_DIR)
    with conn.cursor() as cur:
        for cover in covers:
            if cover and cover.startswith("user/") and not cover_in_use(cur, cover):
                remove_file(COVER_ROOT / cover, USER_COVER_DIR)
    return True
