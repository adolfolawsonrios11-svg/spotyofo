"""Compartir canciones y discos: enlaces /s/<token> y envíos a otra cuenta («Compartidos conmigo»).

Los enlaces exigen sesión: el administrador aprueba cada cuenta, así que «tener el enlace» no abre nada a
desconocidos. La biblioteca ya es común a todas las cuentas; compartir es una vía directa (enlace o bandeja),
no un permiso nuevo. Solo la persona que comparte (o el administrador) puede revocar; quien recibe puede quitarlo de su bandeja.
"""

import secrets

import accounts

KINDS = ("track", "album")
SHARES_PER_WINDOW = 40
SHARES_WINDOW = 60
INBOX_LIMIT = 100


class ShareError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def new_token():
    return secrets.token_urlsafe(16)  # 22 caracteres, 128 bits


def target_exists(conn, kind, target_id):
    table = "tracks" if kind == "track" else "albums"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {table} WHERE id = %s", (target_id,))
        return cur.fetchone() is not None


def clean_kind(value):
    kind = str(value or "").strip().lower()
    if kind not in KINDS:
        raise ShareError("Solo se pueden compartir canciones y discos.")
    return kind


def clean_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ShareError("Elemento no válido.") from None
    if number < 1 or number > 2**31 - 1:
        raise ShareError("Elemento no válido.")
    return number


def find_recipient(conn, username, owner_id):
    name = accounts.normalize_username(username)
    if not name or not accounts.USERNAME_RE.match(name):
        raise ShareError("No encontré a ese usuario.", 404)
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, display_name, status, is_demo FROM users WHERE username = %s", (name,))
        row = cur.fetchone()
    if not row or row["status"] != "active" or row["is_demo"]:
        raise ShareError("No encontré a ese usuario.", 404)
    if row["id"] == owner_id:
        raise ShareError("Ese eres tú. Para ti basta con el enlace.", 400)
    return row


def create(conn, owner_id, kind, target_id, to_user_id=None):
    """Crea (o reutiliza) el enlace. Devuelve la fila de shares."""
    kind = clean_kind(kind)
    target_id = clean_id(target_id)
    if not target_exists(conn, kind, target_id):
        raise ShareError("No encontré eso para compartir.", 404)
    with conn.cursor() as cur:
        if to_user_id is None:
            cur.execute(
                "SELECT * FROM shares WHERE owner_id = %s AND kind = %s AND target_id = %s AND to_user_id IS NULL LIMIT 1",
                (owner_id, kind, target_id),
            )
        else:
            cur.execute(
                "SELECT * FROM shares WHERE owner_id = %s AND kind = %s AND target_id = %s AND to_user_id = %s LIMIT 1",
                (owner_id, kind, target_id, to_user_id),
            )
        row = cur.fetchone()
        if row:
            if to_user_id is not None:
                # Volver a enviar lo deja como nuevo en su bandeja.
                cur.execute("UPDATE shares SET seen_at = NULL, created_at = NOW() WHERE id = %s", (row["id"],))
            return row
        token = new_token()
        cur.execute(
            "INSERT INTO shares (token, kind, target_id, owner_id, to_user_id) VALUES (%s, %s, %s, %s, %s)",
            (token, kind, target_id, owner_id, to_user_id),
        )
        cur.execute("SELECT * FROM shares WHERE id = %s", (cur.lastrowid,))
        return cur.fetchone()


def get(conn, token):
    token = str(token or "")
    if len(token) != 22:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.*, u.username AS owner_username, u.display_name AS owner_name
            FROM shares s JOIN users u ON u.id = s.owner_id
            WHERE s.token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
    if not row or not target_exists(conn, row["kind"], row["target_id"]):
        return None
    return row


def mark_seen(conn, share, user_id):
    if share["to_user_id"] == user_id and share["seen_at"] is None:
        with conn.cursor() as cur:
            cur.execute("UPDATE shares SET seen_at = NOW() WHERE id = %s", (share["id"],))


def revoke(conn, token, user_id, is_admin):
    """El dueño o el administrador lo revoca; quien lo recibió lo quita de su bandeja."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, owner_id, to_user_id FROM shares WHERE token = %s", (str(token or ""),))
        row = cur.fetchone()
        if not row:
            raise ShareError("Ese enlace ya no existe.", 404)
        if not (is_admin or row["owner_id"] == user_id or row["to_user_id"] == user_id):
            raise ShareError("No puedes quitar este enlace.", 403)
        cur.execute("DELETE FROM shares WHERE id = %s", (row["id"],))


def inbox(conn, user_id):
    """Lo que otras cuentas le enviaron, lo más reciente primero, con título para mostrar."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.token, s.kind, s.target_id, s.created_at, s.seen_at,
                   u.username AS owner_username, u.display_name AS owner_name,
                   t.title AS track_title, ta.name AS track_artist, tal.cover_file AS track_cover,
                   al.title AS album_title, aa.name AS album_artist, al.cover_file AS album_cover
            FROM shares s
            JOIN users u ON u.id = s.owner_id
            LEFT JOIN tracks t ON s.kind = 'track' AND t.id = s.target_id
            LEFT JOIN artists ta ON ta.id = t.artist_id
            LEFT JOIN albums tal ON tal.id = t.album_id
            LEFT JOIN albums al ON s.kind = 'album' AND al.id = s.target_id
            LEFT JOIN artists aa ON aa.id = al.artist_id
            WHERE s.to_user_id = %s
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT %s
            """,
            (user_id, INBOX_LIMIT),
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        track = row["kind"] == "track"
        title = row["track_title"] if track else row["album_title"]
        if title is None:
            continue  # lo compartido ya no existe
        cover = row["track_cover"] if track else row["album_cover"]
        items.append({
            "token": row["token"],
            "kind": row["kind"],
            "id": row["target_id"],
            "title": title,
            "artist": row["track_artist"] if track else row["album_artist"],
            "cover_url": f"/static/covers/{cover}",
            "from_username": row["owner_username"],
            "from_name": row["owner_name"] or row["owner_username"],
            "shared_at": row["created_at"].isoformat(sep=" ", timespec="seconds") if row["created_at"] else None,
            "unseen": row["seen_at"] is None,
            "path": f"/s/{row['token']}",
        })
    return items


def unseen_count(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM shares s
            LEFT JOIN tracks t ON s.kind = 'track' AND t.id = s.target_id
            LEFT JOIN albums al ON s.kind = 'album' AND al.id = s.target_id
            WHERE s.to_user_id = %s AND s.seen_at IS NULL AND (t.id IS NOT NULL OR al.id IS NOT NULL)
            """,
            (user_id,),
        )
        return int(cur.fetchone()["n"])


def purge_target(cur, kind, target_id):
    """Quita los enlaces de algo que se eliminó (se llama dentro de la transacción de la baja)."""
    cur.execute("DELETE FROM shares WHERE kind = %s AND target_id = %s", (kind, target_id))
