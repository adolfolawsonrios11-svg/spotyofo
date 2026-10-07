"""Edición de canciones (solo administradores): título, artista, álbum, año, número de pista y género.

Solo cambia la base de datos. Los archivos de audio y sus etiquetas (ID3, etc.) quedan como se subieron.
Las pistas conservan su id, así que Me gusta, listas e historial no cambian.

Artista y álbum: si el nombre nuevo ya existe se reutiliza; si no, se crea. Cuando se renombra un álbum
completo (todas sus pistas a la vez) se renombra en su lugar y conserva la portada. Los álbumes y artistas
que quedan vacíos se borran igual que al eliminar una canción (DELETE /api/tracks/<id>).
"""

import datetime
import re

import uploads

# Campos permitidos: nombre -> largo máximo (texto) o None (número).
TEXT_FIELDS = {"title": 160, "artist": 120, "album": 160, "genre": 80}
NUMBER_FIELDS = {"year", "track_number"}
ALL_FIELDS = set(TEXT_FIELDS) | NUMBER_FIELDS
BATCH_FIELDS = {"artist", "album", "year", "genre"}  # en lote no tiene sentido un título o número comunes
MAX_BATCH = 500
MIN_YEAR = 1000
MAX_TRACK_NUMBER = 9999
NO_GENRE = "Sin género"
GENERATED_ALBUM_COVER = re.compile(r"^user/album-(\d+)\.svg$")


class EditError(Exception):
    def __init__(self, message, code=400, field=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def max_year():
    return datetime.date.today().year + 1


def clean_changes(raw, batch=False):
    """Valida el cuerpo JSON. Devuelve dict solo con los campos enviados (ya limpios)."""
    if not isinstance(raw, dict) or not raw:
        raise EditError("No hay cambios que guardar.")
    allowed = BATCH_FIELDS if batch else ALL_FIELDS
    unknown = sorted(set(raw) - ALL_FIELDS)
    if unknown:
        raise EditError(f"Campo no válido: {unknown[0]}.", field=unknown[0])
    blocked = sorted(set(raw) - allowed)
    if blocked:
        raise EditError("En la edición por lote solo se cambia artista, álbum, año o género.", field=blocked[0])
    out = {}
    for name, limit in TEXT_FIELDS.items():
        if name not in raw:
            continue
        value = raw[name]
        if not isinstance(value, str):
            raise EditError(f"El campo {name} debe ser texto.", field=name)
        text = uploads.clean_text(value, 10_000)
        if len(text) > limit:
            raise EditError(f"El campo {name} admite hasta {limit} caracteres.", field=name)
        if not text:
            if name == "genre":
                text = NO_GENRE
            else:
                raise EditError(f"El campo {name} no puede quedar vacío.", field=name)
        out[name] = text
    for name in NUMBER_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if value in ("", None):
            value = 0
        if isinstance(value, bool):
            raise EditError(f"El campo {name} debe ser un número.", field=name)
        if isinstance(value, str) and re.fullmatch(r"\s*\d{1,5}\s*", value):
            value = int(value)
        if not isinstance(value, int):
            raise EditError(f"El campo {name} debe ser un número entero.", field=name)
        if name == "year" and value and not (MIN_YEAR <= value <= max_year()):
            raise EditError(f"El año debe estar entre {MIN_YEAR} y {max_year()} (o vacío).", field=name)
        if name == "track_number" and not (0 <= value <= MAX_TRACK_NUMBER):
            raise EditError(f"El número de pista debe estar entre 0 y {MAX_TRACK_NUMBER}.", field=name)
        out[name] = value
    return out


def clean_ids(raw):
    if not isinstance(raw, list) or not raw:
        raise EditError("Elige al menos una canción.")
    ids = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise EditError("Lista de canciones no válida.")
        if value not in ids:
            ids.append(value)
    if len(ids) > MAX_BATCH:
        raise EditError(f"Se pueden editar hasta {MAX_BATCH} canciones a la vez.")
    return ids


def _find_album(cur, artist_id, title):
    cur.execute(
        "SELECT id, cover_file, year FROM albums WHERE source = 'user' AND artist_id = %s AND title = %s LIMIT 1",
        (artist_id, title),
    )
    return cur.fetchone()


def edit_tracks(conn, ids, changes):
    """Aplica changes a las pistas ids en una transacción. Devuelve un resumen."""
    placeholders = ", ".join(["%s"] * len(ids))
    covers = []
    summary = {"updated": 0, "albums_created": 0, "albums_renamed": 0, "albums_removed": 0, "artists_created": 0, "artists_removed": 0}
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT t.id, t.source, t.artist_id, t.album_id, t.genre,
                       al.artist_id AS album_artist_id, al.title AS album_title, al.year AS album_year
                FROM tracks t JOIN albums al ON al.id = t.album_id
                WHERE t.id IN ({placeholders})
                FOR UPDATE
                """,
                tuple(ids),
            )
            rows = {row["id"]: row for row in cur.fetchall()}
            missing = [track_id for track_id in ids if track_id not in rows]
            if missing:
                raise EditError(f"Canción no encontrada (id {missing[0]}).", 404)
            if any(row["source"] != "user" for row in rows.values()):
                raise EditError("Solo se pueden editar canciones subidas.", 403)

            old_albums = {row["album_id"] for row in rows.values()}
            old_artists = {row["artist_id"] for row in rows.values()} | {row["album_artist_id"] for row in rows.values()}

            # 1) campos propios de la pista
            simple = {key: changes[key] for key in ("title", "track_number", "genre") if key in changes}
            if simple:
                sets = ", ".join(f"{key} = %s" for key in simple)
                cur.execute(f"UPDATE tracks SET {sets} WHERE id IN ({placeholders})", (*simple.values(), *ids))

            # 2) artista de la pista
            new_artist_id = None
            if "artist" in changes:
                cur.execute("SELECT id, name FROM artists WHERE source = 'user' AND name = %s LIMIT 1", (changes["artist"],))
                found = cur.fetchone()
                if found:
                    new_artist_id = found["id"]
                    if found["name"] != changes["artist"]:
                        # mismo artista con otra escritura (mayúsculas/tildes): se corrige el nombre
                        cur.execute("UPDATE artists SET name = %s WHERE id = %s", (changes["artist"], found["id"]))
                else:
                    genre = changes.get("genre") or next(iter(rows.values()))["genre"]
                    new_artist_id = uploads.find_or_create_artist(cur, changes["artist"], genre)
                    summary["artists_created"] += 1

            # 3) álbum de destino de cada pista (el álbum es del «artista del álbum»)
            groups = {}
            for row in rows.values():
                album_artist = row["album_artist_id"]
                if new_artist_id and row["album_artist_id"] == row["artist_id"]:
                    album_artist = new_artist_id  # el álbum era del mismo artista: lo sigue
                title = changes.get("album", row["album_title"])
                if album_artist == row["album_artist_id"] and title == row["album_title"]:
                    key = (row["album_id"], None, None)  # sigue en su álbum
                else:
                    key = (row["album_id"], album_artist, title)
                groups.setdefault(key, []).append(row["id"])

            year = changes.get("year")
            for (old_album_id, target_artist, target_title), track_ids in groups.items():
                target_id = old_album_id
                if target_artist is not None:
                    existing = _find_album(cur, target_artist, target_title)
                    if existing and existing["id"] != old_album_id:
                        target_id = existing["id"]
                    elif existing:
                        # el mismo álbum con otra escritura (mayúsculas/tildes): se corrige el título
                        cur.execute("UPDATE albums SET title = %s WHERE id = %s", (target_title, old_album_id))
                        summary["albums_renamed"] += 1
                    else:
                        cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE album_id = %s", (old_album_id,))
                        whole = cur.fetchone()["n"] == len(track_ids)
                        cur.execute("SELECT id, cover_file, year, source, accent_a, accent_b FROM albums WHERE id = %s", (old_album_id,))
                        old = cur.fetchone()
                        if whole and old["source"] == "user":
                            # renombrar en su lugar: conserva la portada (y la regenera si es la inicial automática)
                            cur.execute(
                                "UPDATE albums SET title = %s, artist_id = %s WHERE id = %s",
                                (target_title, target_artist, old_album_id),
                            )
                            if GENERATED_ALBUM_COVER.match(old["cover_file"] or ""):
                                uploads.write_initial_cover(old["cover_file"], old["accent_a"], old["accent_b"], target_title)
                            summary["albums_renamed"] += 1
                        else:
                            target_id, _cover = uploads.find_or_create_album(cur, target_artist, target_title, year or old["year"] or 0)
                            summary["albums_created"] += 1
                            if old["cover_file"] and not old["cover_file"].endswith(".svg"):
                                # parte de un álbum con portada real: el álbum nuevo hereda la misma imagen
                                cur.execute("UPDATE albums SET cover_file = %s WHERE id = %s", (old["cover_file"], target_id))
                                if _cover.startswith("user/"):
                                    covers.append(_cover)
                if year is not None:
                    cur.execute("UPDATE albums SET year = %s WHERE id = %s AND source = 'user'", (year, target_id))
                sets, params = [], []
                if target_id != old_album_id:
                    sets.append("album_id = %s")
                    params.append(target_id)
                if new_artist_id:
                    sets.append("artist_id = %s")
                    params.append(new_artist_id)
                if sets:
                    marks = ", ".join(["%s"] * len(track_ids))
                    cur.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id IN ({marks})", (*params, *track_ids))

            # 4) álbumes y artistas que quedaron vacíos (igual que al eliminar una canción)
            for album_id in old_albums:
                cur.execute("SELECT COUNT(*) AS n FROM tracks WHERE album_id = %s", (album_id,))
                if cur.fetchone()["n"] == 0:
                    cur.execute("SELECT cover_file, artist_id FROM albums WHERE id = %s AND source = 'user'", (album_id,))
                    album = cur.fetchone()
                    if album:
                        cur.execute("DELETE FROM albums WHERE id = %s", (album_id,))
                        covers.append(album["cover_file"])
                        old_artists.add(album["artist_id"])
                        summary["albums_removed"] += 1
            for artist_id in old_artists:
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
                        summary["artists_removed"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    summary["updated"] = len(ids)
    remove_unused_covers(conn, covers)
    return summary


def remove_unused_covers(conn, covers):
    with conn.cursor() as cur:
        for cover in set(covers):
            if cover and cover.startswith("user/") and not uploads.cover_in_use(cur, cover):
                uploads.remove_file(uploads.COVER_ROOT / cover, uploads.USER_COVER_DIR)


ALLOWED_COVER_EXT = {"jpg", "png", "webp"}


def set_album_cover(conn, album_id, storage):
    """Reemplaza la portada de un álbum subido por una imagen JPG, PNG o WEBP (hasta 12 MB)."""
    if storage is None or not storage.filename:
        raise EditError("Falta la imagen.")
    data = storage.stream.read(uploads.MAX_COVER_BYTES + 1)
    if len(data) > uploads.MAX_COVER_BYTES:
        raise EditError("La imagen supera 12 MB.", 413)
    ext = uploads._image_ext(data) if data else None
    if ext not in ALLOWED_COVER_EXT:
        raise EditError("Usa una imagen JPG, PNG o WEBP.", 415)
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, cover_file FROM albums WHERE id = %s", (album_id,))
        album = cur.fetchone()
    if not album:
        raise EditError("Álbum no encontrado.", 404)
    if album["source"] != "user":
        raise EditError("Solo se puede cambiar la portada de álbumes subidos.", 403)
    uploads.ensure_dirs()
    new_cover = uploads.save_cover(data)
    old_cover = album["cover_file"]
    with conn.cursor() as cur:
        cur.execute("UPDATE albums SET cover_file = %s WHERE id = %s", (new_cover, album_id))
    if old_cover != new_cover:
        remove_unused_covers(conn, [old_cover])
    return new_cover
