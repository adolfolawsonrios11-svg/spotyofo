"""Listas propias: agregar, quitar y ordenar pistas.

Cada lista mezcla pistas del catálogo o subidas (playlist_tracks) y archivos de la carpeta
Música (device_playlist_tracks). El orden es uno solo para ambas tablas: la columna
position. Toda escritura bloquea la fila de la lista (SELECT ... FOR UPDATE) dentro de una
transacción, así dos pestañas que agregan a la vez nunca repiten posición.

Las claves de pista que viajan al navegador son «t:<id>» (catálogo o subida) y
«d:<ruta relativa>» (archivo de la carpeta Música).

Cuentas: cada lista propia tiene dueño (owner_id). Solo su dueño la ve y la edita; una
lista ajena responde 404 (no se revela que existe). Las editoriales son de todos y de solo
lectura. Los archivos de la carpeta Música (claves «d:») son solo para administradores.
"""

import html
from contextlib import contextmanager
from pathlib import Path

import devicelibrary
from localfiles import LocalFileError

MAX_ITEMS_PER_REQUEST = 1000
MAX_PLAYLIST_TRACKS = 5000
MAX_PATH = 512
COVERS_DIR = Path(__file__).resolve().parent / "static" / "covers"
PALETTE = [
    ("#12343c", "#7dffe0"),
    ("#241018", "#ff6d93"),
    ("#1a1430", "#b7a6ff"),
    ("#3a2410", "#ffc56d"),
    ("#241612", "#ff7358"),
    ("#10243a", "#8fd4ff"),
]


def write_playlist_cover(filename, c1, c2, label):
    initial = html.escape((label[:1] or "S").upper())
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="640" height="640" fill="url(#bg)"/>
  <circle cx="460" cy="180" r="90" fill="#ffffff" fill-opacity="0.18"/>
  <circle cx="180" cy="460" r="120" fill="#000000" fill-opacity="0.18"/>
  <text x="72" y="430" fill="#ffffff" font-family="Segoe UI, sans-serif" font-size="280" font-weight="700">{initial}</text>
</svg>
"""
    path = COVERS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


class PlaylistError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


@contextmanager
def transaction(conn):
    conn.begin()
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _lock(cur, playlist_id, user_id):
    cur.execute("SELECT id, name, editorial, owner_id FROM playlists WHERE id = %s FOR UPDATE", (playlist_id,))
    row = cur.fetchone()
    if not row:
        raise PlaylistError("Lista no encontrada.", 404)
    if row["editorial"]:
        raise PlaylistError("Las listas editoriales están cerradas. Crea una lista tuya.", 403)
    if row["owner_id"] != user_id:
        raise PlaylistError("Lista no encontrada.", 404)
    return row


def can_view(cur, playlist_id, user_id):
    cur.execute("SELECT editorial, owner_id FROM playlists WHERE id = %s", (playlist_id,))
    row = cur.fetchone()
    return bool(row) and (bool(row["editorial"]) or row["owner_id"] == user_id)


DEVICE_ONLY_ADMIN = "Los archivos de la carpeta Música del servidor son solo para el administrador."


def _order(cur, playlist_id):
    """Orden actual, con el mismo criterio que la vista de la lista."""
    cur.execute(
        """
        SELECT 't' AS kind, pt.track_id AS id, pt.position, pt.added_at, t.title
        FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id
        WHERE pt.playlist_id = %s
        UNION ALL
        SELECT 'd' AS kind, dpt.track_id AS id, dpt.position, dpt.added_at, d.title
        FROM device_playlist_tracks dpt JOIN device_tracks d ON d.id = dpt.track_id
        WHERE dpt.playlist_id = %s
        """,
        (playlist_id, playlist_id),
    )
    rows = list(cur.fetchall())
    rows.sort(key=lambda row: (row["position"] or 0, str(row["added_at"] or ""), row["title"]))
    return rows


def _table(kind):
    return "playlist_tracks" if kind == "t" else "device_playlist_tracks"


def _write_positions(cur, playlist_id, items):
    """Deja las posiciones 0..n-1 en el orden dado (solo toca las filas que cambian)."""
    for index, row in enumerate(items):
        if row.get("position") != index:
            cur.execute(
                f"UPDATE {_table(row['kind'])} SET position = %s WHERE playlist_id = %s AND track_id = %s",
                (index, playlist_id, row["id"]),
            )
            row["position"] = index


def _keys_for(cur, items):
    """Convierte (kind, id) en claves «t:1» / «d:ruta» para el navegador."""
    device_ids = [item_id for kind, item_id in items if kind == "d"]
    paths = {}
    if device_ids:
        marks = ",".join(["%s"] * len(device_ids))
        cur.execute(f"SELECT id, rel_path FROM device_tracks WHERE id IN ({marks})", device_ids)
        paths = {row["id"]: row["rel_path"] for row in cur.fetchall()}
    return [f"t:{item_id}" if kind == "t" else f"d:{paths.get(item_id, '')}" for kind, item_id in items]


def _count(cur, playlist_id):
    cur.execute(
        """
        SELECT (SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = %s)
             + (SELECT COUNT(*) FROM device_playlist_tracks WHERE playlist_id = %s) AS total
        """,
        (playlist_id, playlist_id),
    )
    return int(cur.fetchone()["total"])


def _unique(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _catalog_ids(cur, ids):
    if not ids:
        return []
    marks = ",".join(["%s"] * len(ids))
    cur.execute(f"SELECT id FROM tracks WHERE id IN ({marks})", ids)
    found = {row["id"] for row in cur.fetchall()}
    missing = [track_id for track_id in ids if track_id not in found]
    if missing:
        raise PlaylistError("Una de las pistas ya no existe. Recarga la página.", 404)
    return [("t", track_id) for track_id in ids]


def _device_ids(cur, paths):
    """Rutas de la carpeta Música -> ids de device_tracks (guarda las que falten)."""
    items = []
    for raw in paths:
        path = str(raw or "").replace("\\", "/").strip("/")
        if not path or len(path) > MAX_PATH:
            raise PlaylistError("Ruta de archivo no válida.")
        cur.execute("SELECT id FROM device_tracks WHERE rel_path = %s", (path,))
        row = cur.fetchone()
        if row:
            items.append(("d", row["id"]))
            continue
        try:
            tracks = devicelibrary._require_tracks(path)
        except LocalFileError as exc:
            raise PlaylistError(exc.message, exc.code) from exc
        except devicelibrary.LibraryError as exc:
            raise PlaylistError(exc.message, exc.code) from exc
        for track in tracks:
            items.append(("d", devicelibrary._save(cur, track)))
    return items


def _parse_int(value, message):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlaylistError(message) from exc
    if number <= 0:
        raise PlaylistError(message)
    return number


def resolve_items(cur, body, target_id=None, user_id=None, allow_device=False):
    """Lee qué pistas agregar desde el cuerpo JSON. Devuelve [(kind, id)] sin repetidos, en orden."""
    items = []
    if not allow_device and (body.get("paths") is not None or any(
        str(key or "").startswith("d:") for key in (body.get("keys") or []) if isinstance(body.get("keys"), list)
    )):
        raise PlaylistError(DEVICE_ONLY_ADMIN, 403)
    if body.get("track_id") is not None:
        items += _catalog_ids(cur, [_parse_int(body.get("track_id"), "Falta la pista.")])
    if body.get("track_ids") is not None:
        raw = body.get("track_ids")
        if not isinstance(raw, list):
            raise PlaylistError("Lista de pistas no válida.")
        if len(raw) > MAX_ITEMS_PER_REQUEST:
            raise PlaylistError(f"Se pueden agregar hasta {MAX_ITEMS_PER_REQUEST} pistas a la vez.")
        items += _catalog_ids(cur, _unique([_parse_int(value, "Lista de pistas no válida.") for value in raw]))
    if body.get("keys") is not None:
        raw = body.get("keys")
        if not isinstance(raw, list) or len(raw) > MAX_ITEMS_PER_REQUEST:
            raise PlaylistError(f"Se pueden agregar hasta {MAX_ITEMS_PER_REQUEST} pistas a la vez.")
        for key in raw:
            text = str(key or "")
            if text.startswith("t:"):
                items += _catalog_ids(cur, [_parse_int(text[2:], "Pista no válida.")])
            elif text.startswith("d:"):
                items += _device_ids(cur, [text[2:]])
            else:
                raise PlaylistError("Pista no válida.")
    if body.get("paths") is not None:
        raw = body.get("paths")
        if not isinstance(raw, list) or len(raw) > MAX_ITEMS_PER_REQUEST:
            raise PlaylistError("Lista de archivos no válida.")
        items += _device_ids(cur, raw)
    source = body.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise PlaylistError("Origen no válido.")
        kind = source.get("type")
        source_id = _parse_int(source.get("id"), "Origen no válido.")
        if kind == "album":
            cur.execute("SELECT id FROM tracks WHERE album_id = %s ORDER BY track_number, id", (source_id,))
            rows = cur.fetchall()
            if not rows:
                raise PlaylistError("Disco no encontrado.", 404)
            items += [("t", row["id"]) for row in rows]
        elif kind == "playlist":
            if target_id is not None and source_id == target_id:
                raise PlaylistError("Elige una lista distinta a la de origen.")
            if not can_view(cur, source_id, user_id):
                raise PlaylistError("Lista de origen no encontrada.", 404)
            items += [
                (row["kind"], row["id"]) for row in _order(cur, source_id) if allow_device or row["kind"] == "t"
            ]
        else:
            raise PlaylistError("Origen no válido.")
    items = _unique(items)
    if not items:
        raise PlaylistError("No hay pistas para agregar.")
    if len(items) > MAX_ITEMS_PER_REQUEST:
        raise PlaylistError(f"Se pueden agregar hasta {MAX_ITEMS_PER_REQUEST} pistas a la vez.")
    return items


def add_items(conn, playlist_id, body, user_id, allow_device=False):
    """Agrega pistas al final (o en «position»), sin repetir. Una sola transacción."""
    position = body.get("position")
    if position is not None:
        try:
            position = int(position)
        except (TypeError, ValueError) as exc:
            raise PlaylistError("Posición no válida.") from exc
        if position < 0:
            raise PlaylistError("Posición no válida.")
    with transaction(conn), conn.cursor() as cur:
        playlist = _lock(cur, playlist_id, user_id)
        return _add_locked(cur, playlist, resolve_items(cur, body, playlist_id, user_id, allow_device), position)


def _add_locked(cur, playlist, items, position=None):
    playlist_id = playlist["id"]
    order = _order(cur, playlist_id)
    present = {(row["kind"], row["id"]) for row in order}
    fresh = [item for item in items if item not in present]
    skipped = len(items) - len(fresh)
    if len(order) + len(fresh) > MAX_PLAYLIST_TRACKS:
        raise PlaylistError(f"Una lista admite hasta {MAX_PLAYLIST_TRACKS} pistas.")
    if fresh:
        if position is None or position >= len(order):
            start = max((row["position"] or 0 for row in order), default=-1) + 1
            start = max(start, len(order))
        else:
            _write_positions(cur, playlist_id, order)
            start = position
            for row in reversed(order[position:]):
                cur.execute(
                    f"UPDATE {_table(row['kind'])} SET position = position + %s WHERE playlist_id = %s AND track_id = %s",
                    (len(fresh), playlist_id, row["id"]),
                )
        for offset, (kind, item_id) in enumerate(fresh):
            cur.execute(
                f"INSERT INTO {_table(kind)} (playlist_id, track_id, position) VALUES (%s, %s, %s)",
                (playlist_id, item_id, start + offset),
            )
    return {
        "added": len(fresh),
        "skipped": skipped,
        "added_keys": _keys_for(cur, fresh),
        "playlist_id": playlist_id,
        "playlist_name": playlist["name"],
        "track_count": len(order) + len(fresh),
    }


def create_with_items(conn, create_row, body, user_id, allow_device=False):
    """Crea la lista (create_row escribe la fila y la portada) y agrega las pistas, todo junto."""
    with transaction(conn), conn.cursor() as cur:
        new_id = create_row(cur)
        playlist = _lock(cur, new_id, user_id)
        wants_items = any(body.get(key) is not None for key in ("track_id", "track_ids", "keys", "paths", "source"))
        result = _add_locked(cur, playlist, resolve_items(cur, body, new_id, user_id, allow_device)) if wants_items else {
            "added": 0, "skipped": 0, "added_keys": [], "playlist_id": new_id,
            "playlist_name": playlist["name"], "track_count": 0,
        }
    return new_id, result


def _key_items(cur, keys):
    """Claves «t:» / «d:» -> (kind, id) existentes, sin crear nada."""
    items = []
    for key in keys:
        text = str(key or "")
        if text.startswith("t:"):
            items.append(("t", _parse_int(text[2:], "Pista no válida.")))
        elif text.startswith("d:"):
            path = text[2:].replace("\\", "/").strip("/")
            cur.execute("SELECT id FROM device_tracks WHERE rel_path = %s", (path,))
            row = cur.fetchone()
            items.append(("d", row["id"] if row else 0))
        else:
            raise PlaylistError("Pista no válida.")
    return items


def remove_items(conn, playlist_id, keys, user_id):
    """Quita pistas. Devuelve dónde estaba la primera, para poder deshacer."""
    if not isinstance(keys, list) or not keys or len(keys) > MAX_ITEMS_PER_REQUEST:
        raise PlaylistError("Elige qué pistas quitar.")
    with transaction(conn), conn.cursor() as cur:
        playlist = _lock(cur, playlist_id, user_id)
        order = _order(cur, playlist_id)
        index = {(row["kind"], row["id"]): pos for pos, row in enumerate(order)}
        items = _key_items(cur, keys)
        removed = [item for item in _unique(items) if item in index]
        first = min((index[item] for item in removed), default=None)
        for kind, item_id in removed:
            cur.execute(f"DELETE FROM {_table(kind)} WHERE playlist_id = %s AND track_id = %s", (playlist_id, item_id))
        left = [row for row in order if (row["kind"], row["id"]) not in set(removed)]
        _write_positions(cur, playlist_id, left)
        return {
            "removed": len(removed),
            "position": first,
            "playlist_id": playlist_id,
            "playlist_name": playlist["name"],
            "track_count": len(left),
        }


def reorder(conn, playlist_id, keys, user_id):
    """Guarda un orden nuevo. Debe incluir exactamente las mismas pistas que la lista."""
    if not isinstance(keys, list) or len(keys) > MAX_PLAYLIST_TRACKS:
        raise PlaylistError("Orden no válido.")
    with transaction(conn), conn.cursor() as cur:
        playlist = _lock(cur, playlist_id, user_id)
        order = _order(cur, playlist_id)
        items = _key_items(cur, keys)
        by_item = {(row["kind"], row["id"]): row for row in order}
        if len(items) != len(order) or len(set(items)) != len(items) or set(items) != set(by_item):
            raise PlaylistError("La lista cambió mientras la ordenabas. Se recargó el orden actual.", 409)
        _write_positions(cur, playlist_id, [by_item[item] for item in items])
        return {"ok": True, "playlist_id": playlist_id, "playlist_name": playlist["name"], "track_count": len(order)}


def own_playlists(conn, user_id, track_id=None, path=None):
    """Listas propias del usuario con cantidad de pistas y si ya contienen la pista dada."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.name, p.description, p.editorial, p.accent_a, p.accent_b, p.cover_file, p.created_at,
                   (SELECT COUNT(*) FROM playlist_tracks x WHERE x.playlist_id = p.id)
                 + (SELECT COUNT(*) FROM device_playlist_tracks y WHERE y.playlist_id = p.id) AS track_count
            FROM playlists p
            WHERE p.editorial = 0 AND p.owner_id = %s
            ORDER BY p.id DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
        contains = set()
        if track_id:
            cur.execute("SELECT playlist_id FROM playlist_tracks WHERE track_id = %s", (track_id,))
            contains = {row["playlist_id"] for row in cur.fetchall()}
        elif path:
            cur.execute(
                """
                SELECT dpt.playlist_id FROM device_playlist_tracks dpt
                JOIN device_tracks d ON d.id = dpt.track_id WHERE d.rel_path = %s
                """,
                (path,),
            )
            contains = {row["playlist_id"] for row in cur.fetchall()}
    for row in rows:
        row["track_count"] = int(row["track_count"])
        row["contains"] = row["id"] in contains
    return rows
