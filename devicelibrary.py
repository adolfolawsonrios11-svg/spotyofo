"""Me gusta (de cada usuario) y listas para archivos de la carpeta Música. No copia el audio."""

from datetime import datetime

from localfiles import AUDIO_EXT, IMAGE_EXT, LocalFileError, collect_tracks, resolve_inside, saved_track
from songs import song_key


class LibraryError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def _clip(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit]


def _require_tracks(relative):
    try:
        tracks = collect_tracks(relative or "")
    except LocalFileError:
        raise
    if not tracks:
        raise LibraryError("Esta carpeta no tiene pistas.")
    for track in tracks:
        if len(track["id"]) > 512:
            raise LibraryError("La ruta de un archivo es demasiado larga para guardarla.")
    return tracks


def _save(cur, track):
    cur.execute(
        """
        INSERT INTO device_tracks
          (rel_path, title, artist, album, duration_ms, track_number, cover_rel, folder_rel)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          title = VALUES(title),
          artist = VALUES(artist),
          album = VALUES(album),
          duration_ms = VALUES(duration_ms),
          track_number = VALUES(track_number),
          cover_rel = VALUES(cover_rel),
          folder_rel = VALUES(folder_rel)
        """,
        (
            track["id"],
            _clip(track["title"], 180) or "Sin título",
            _clip(track["artist"], 180) or "Archivo de equipo",
            _clip(track["album"], 180) or "Música",
            max(0, int(track["duration_ms"] or 0)),
            max(0, min(int(track["track_number"] or 0), 32767)),
            _clip(track.get("cover_path") or "", 512) or None,
            _clip(track.get("folder") or "", 512),
        ),
    )
    cur.execute("SELECT id FROM device_tracks WHERE rel_path = %s", (track["id"],))
    return cur.fetchone()["id"]


def _like_rows(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 'catalog' AS origin, l.track_id AS ref, l.liked_at, t.title, a.name AS artist, t.duration_ms
            FROM user_likes l
            JOIN tracks t ON t.id = l.track_id
            JOIN artists a ON a.id = t.artist_id
            WHERE l.user_id = %s
            UNION ALL
            SELECT 'device', l.track_id, l.liked_at, t.title, t.artist, t.duration_ms
            FROM user_device_likes l
            JOIN device_tracks t ON t.id = l.track_id
            WHERE l.user_id = %s
            """,
            (user_id, user_id),
        )
        return cur.fetchall()


def dedupe_likes(conn, user_id):
    """Deja una sola fila de Me gusta por canción (de este usuario). Conserva la más reciente."""
    groups = {}
    for row in _like_rows(conn, user_id):
        key = song_key(row["title"], row["artist"], row["duration_ms"])
        groups.setdefault(key, []).append(row)
    catalog_drop = []
    device_drop = []
    for items in groups.values():
        items.sort(key=lambda item: item["liked_at"] or datetime.min, reverse=True)
        for extra in items[1:]:
            if extra["origin"] == "catalog":
                catalog_drop.append(extra["ref"])
            else:
                device_drop.append(extra["ref"])
    with conn.cursor() as cur:
        if catalog_drop:
            marks = ", ".join(["%s"] * len(catalog_drop))
            cur.execute(f"DELETE FROM user_likes WHERE user_id = %s AND track_id IN ({marks})", [user_id, *catalog_drop])
        if device_drop:
            marks = ", ".join(["%s"] * len(device_drop))
            cur.execute(f"DELETE FROM user_device_likes WHERE user_id = %s AND track_id IN ({marks})", [user_id, *device_drop])
    return len(catalog_drop) + len(device_drop)


def liked_song_keys(conn, user_id):
    dedupe_likes(conn, user_id)
    return {song_key(row["title"], row["artist"], row["duration_ms"]) for row in _like_rows(conn, user_id)}


def apply_song_likes(conn, tracks, user_id):
    if not tracks:
        return
    keys = liked_song_keys(conn, user_id)
    for track in tracks:
        key = track.get("song_key") or song_key(track.get("title"), track.get("artist"), track.get("duration_ms"))
        track["song_key"] = key
        if key in keys:
            track["liked"] = True


def mark_liked(conn, tracks, user_id):
    apply_song_likes(conn, tracks, user_id)
    return bool(tracks) and all(track["liked"] for track in tracks)


def _remove_song(conn, key, user_id):
    catalog_ids = []
    device_ids = []
    for row in _like_rows(conn, user_id):
        if song_key(row["title"], row["artist"], row["duration_ms"]) != key:
            continue
        if row["origin"] == "catalog":
            catalog_ids.append(row["ref"])
        else:
            device_ids.append(row["ref"])
    with conn.cursor() as cur:
        if catalog_ids:
            marks = ", ".join(["%s"] * len(catalog_ids))
            cur.execute(f"DELETE FROM user_likes WHERE user_id = %s AND track_id IN ({marks})", [user_id, *catalog_ids])
        if device_ids:
            marks = ", ".join(["%s"] * len(device_ids))
            cur.execute(f"DELETE FROM user_device_likes WHERE user_id = %s AND track_id IN ({marks})", [user_id, *device_ids])


def _stamp(track):
    track["song_key"] = song_key(track["title"], track["artist"], track["duration_ms"])
    return track["song_key"]


def toggle_catalog_like(conn, track_id, shown, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.title, a.name AS artist, t.duration_ms,
                   IF(l.track_id IS NULL, 0, 1) AS liked
            FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            LEFT JOIN user_likes l ON l.track_id = t.id AND l.user_id = %s
            WHERE t.id = %s
            """,
            (user_id, track_id),
        )
        row = cur.fetchone()
    if not row:
        raise LibraryError("Pista no encontrada.", 404)
    key = song_key(row["title"], row["artist"], row["duration_ms"])
    exact = bool(row["liked"])
    if shown is None:
        shown = exact
    keys = liked_song_keys(conn, user_id)
    if key in keys and (exact or shown):
        _remove_song(conn, key, user_id)
        return {"liked": False, "duplicate": False, "song_key": key, "added": 0, "skipped": 0}
    if key in keys:
        return {"liked": True, "duplicate": True, "song_key": key, "added": 0, "skipped": 1}
    with conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO user_likes (user_id, track_id) VALUES (%s, %s)", (user_id, track_id))
    return {"liked": True, "duplicate": False, "song_key": key, "added": 1, "skipped": 0}


def toggle_saved(conn, relative, shown, user_id):
    tracks = _require_tracks(relative)
    for track in tracks:
        _stamp(track)
    _root, target = resolve_inside(relative or "", AUDIO_EXT | IMAGE_EXT)
    keys = liked_song_keys(conn, user_id)
    if target.is_file():
        track = tracks[0]
        key = track["song_key"]
        exact = key in keys and _device_path_liked(conn, track["id"], user_id)
        if shown is None:
            shown = exact
        if key in keys and (exact or shown):
            _remove_song(conn, key, user_id)
            track["liked"] = False
            return {"liked": False, "duplicate": False, "song_key": key, "added": 0, "skipped": 0, "changed": 1, "tracks": tracks}
        if key in keys:
            track["liked"] = True
            return {"liked": True, "duplicate": True, "song_key": key, "added": 0, "skipped": 1, "changed": 0, "tracks": tracks}
        with conn.cursor() as cur:
            track_id = _save(cur, track)
            cur.execute("INSERT IGNORE INTO user_device_likes (user_id, track_id) VALUES (%s, %s)", (user_id, track_id))
        track["liked"] = True
        return {"liked": True, "duplicate": False, "song_key": key, "added": 1, "skipped": 0, "changed": 1, "tracks": tracks}
    seen = set()
    to_add = []
    skipped = 0
    for track in tracks:
        key = track["song_key"]
        if key in keys or key in seen:
            track["liked"] = True
            skipped += 1
            continue
        seen.add(key)
        to_add.append(track)
    if not to_add:
        for track in tracks:
            _remove_song(conn, track["song_key"], user_id)
            track["liked"] = False
        return {"liked": False, "duplicate": False, "added": 0, "skipped": 0, "changed": len(tracks), "tracks": tracks}
    with conn.cursor() as cur:
        for track in to_add:
            track_id = _save(cur, track)
            cur.execute("INSERT IGNORE INTO user_device_likes (user_id, track_id) VALUES (%s, %s)", (user_id, track_id))
            track["liked"] = True
    return {
        "liked": True,
        "duplicate": False,
        "added": len(to_add),
        "skipped": skipped,
        "changed": len(to_add),
        "tracks": tracks,
    }


def _device_path_liked(conn, rel_path, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.track_id
            FROM device_tracks t
            JOIN user_device_likes l ON l.track_id = t.id AND l.user_id = %s
            WHERE t.rel_path = %s
            """,
            (user_id, rel_path),
        )
        return cur.fetchone() is not None


def liked_tracks(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.*, l.liked_at
            FROM user_device_likes l
            JOIN device_tracks t ON t.id = l.track_id
            WHERE l.user_id = %s
            ORDER BY l.liked_at DESC, t.id DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        track = saved_track(row, liked=True)
        track["_liked_at"] = row["liked_at"]
        items.append(track)
    return items


def playlist_tracks(conn, playlist_id, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.*, pt.position, pt.added_at, IF(l.track_id IS NULL, 0, 1) AS liked
            FROM device_playlist_tracks pt
            JOIN device_tracks t ON t.id = pt.track_id
            LEFT JOIN user_device_likes l ON l.track_id = t.id AND l.user_id = %s
            WHERE pt.playlist_id = %s
            ORDER BY pt.position, pt.added_at
            """,
            (user_id, playlist_id),
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        track = saved_track(row, liked=bool(row["liked"]))
        track["_position"] = row["position"]
        track["_added_at"] = row["added_at"]
        items.append(track)
    return items
