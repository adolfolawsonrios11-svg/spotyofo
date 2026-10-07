"""Cuenta demo de SpotyOFO (portafolio): configuración, alta y reinicio nocturno.

La cuenta demo es una fila normal de users con is_demo = 1, rol «user» y estado «active».
- app.py le niega (403, en el servidor) cambiar la contraseña y todo lo de administrador.
- accounts.authenticate() no la bloquea por intentos fallidos (solo el límite por IP).
- reset() la deja cada noche en su estado inicial: una lista de muestra, algunos Me gusta
  y un poco de historial. Solo toca filas de la cuenta demo (varias verificaciones).

Configuración (variables de entorno o .env; nunca en Git):
  SPOTY_DEMO_USER       usuario de la demo (por defecto «demo»)
  SPOTY_DEMO_PASSWORD   contraseña de la demo. manage.py demo-reset la aplica
  SPOTY_DEMO_ENABLED    1 = botón «Probar demo» en la página de ingreso (por defecto apagado)

Uso: python manage.py demo-reset   (en ofoserv lo ejecuta el timer spotyofo-demo-reset)
"""

import os

import accounts
import config  # noqa: F401  (carga .env antes de leer SPOTY_DEMO_*)
import playlistedit

DEMO_USER = accounts.normalize_username(os.environ.get("SPOTY_DEMO_USER", "demo")) or "demo"
DEMO_ENABLED = os.environ.get("SPOTY_DEMO_ENABLED", "0").strip() == "1"
DEMO_DISPLAY = "Visitante"
DEMO_MAX_PLAYLISTS = 20
SEED_PLAYLIST = "Demo – Lo mejor"
SEED_DESCRIPTION = "Una muestra para que pruebes SpotyOFO. La cuenta demo se reinicia cada noche."
SEED_TRACKS = 10
SEED_PER_ARTIST = 2
SEED_LIKES = 5
SEED_HISTORY = 4


class DemoError(Exception):
    pass


def demo_password():
    return os.environ.get("SPOTY_DEMO_PASSWORD", "")


def find_demo(conn):
    """Fila de la cuenta demo configurada, o None. Solo si está marcada con is_demo = 1."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {accounts.USER_FIELDS} FROM users WHERE username = %s AND is_demo = 1 AND role = 'user'",
            (DEMO_USER,),
        )
        return cur.fetchone()


def ensure_user(conn, password):
    """Crea o repara la cuenta demo: aprobada, sin rol de administrador y con la contraseña dada.

    Nunca convierte en demo una cuenta existente que no lo sea (por ejemplo, alguien que
    se registró como «demo»): en ese caso se detiene con DemoError.
    """
    accounts.validate_username(DEMO_USER)
    accounts.validate_password(password, DEMO_USER)
    with conn.cursor() as cur:
        cur.execute("SELECT id, role, is_demo FROM users WHERE username = %s", (DEMO_USER,))
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE is_demo = 1 AND username <> %s", (DEMO_USER,))
        others = int(cur.fetchone()["n"])
    if others:
        raise DemoError("Hay otra cuenta marcada como demo con otro usuario. Revísalo a mano; no se cambió nada.")
    if row is None:
        user_id = accounts.create_user(conn, DEMO_USER, DEMO_DISPLAY, password, role="user", status="active")
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_demo = 1 WHERE id = %s AND role = 'user'", (user_id,))
        created = True
    else:
        if not row["is_demo"]:
            raise DemoError(f"Ya existe la cuenta «{DEMO_USER}» y no es demo. No se tocó.")
        if row["role"] != "user":
            raise DemoError(f"La cuenta demo «{DEMO_USER}» tiene rol {row['role']}. No se tocó; revísala a mano.")
        user_id = row["id"]
        created = False
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET status = 'active', must_change_password = 0, failed_logins = 0, locked_until = NULL, can_edit = 0,
                display_name = %s, approved_at = COALESCE(approved_at, NOW())
            WHERE id = %s AND is_demo = 1 AND role = 'user'
            """,
            (DEMO_DISPLAY, user_id),
        )
    password_changed = False
    if not accounts.verify_password(conn, user_id, password):
        accounts.set_password(conn, user_id, password, DEMO_USER)
        password_changed = True
    return user_id, created, password_changed


def _pick_seed(cur):
    """Pistas para la lista de muestra: las más escuchadas, variando artistas (máximo 2 por
    artista mientras alcance; si el catálogo tiene pocos artistas, se completa con el resto)."""
    cur.execute("SELECT id, artist_id FROM tracks ORDER BY plays DESC, id ASC LIMIT 500")
    rows = cur.fetchall()
    picked, per_artist = [], {}
    for row in rows:
        if len(picked) >= SEED_TRACKS:
            break
        if per_artist.get(row["artist_id"], 0) < SEED_PER_ARTIST:
            per_artist[row["artist_id"]] = per_artist.get(row["artist_id"], 0) + 1
            picked.append(row["id"])
    for row in rows:
        if len(picked) >= SEED_TRACKS:
            break
        if row["id"] not in picked:
            picked.append(row["id"])
    return picked


def reset(conn, password):
    """Deja la cuenta demo en su estado inicial. Idempotente. Devuelve un resumen."""
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('spotyofo_demo_reset', 30) AS ok")
        if not cur.fetchone()["ok"]:
            raise DemoError("Otro reinicio de la demo está en curso.")
    try:
        user_id, created, password_changed = ensure_user(conn, password)
        old_covers, new_cover = [], None
        conn.begin()
        try:
            with conn.cursor() as cur:
                # Verificación dentro de la transacción: la fila sigue siendo la demo.
                cur.execute(
                    "SELECT id FROM users WHERE id = %s AND username = %s AND is_demo = 1 AND role = 'user' FOR UPDATE",
                    (user_id, DEMO_USER),
                )
                if not cur.fetchone() or not isinstance(user_id, int) or user_id <= 0:
                    raise DemoError("La cuenta demo cambió durante el reinicio. No se tocó nada.")
                cur.execute("SELECT id, cover_file FROM playlists WHERE owner_id = %s AND editorial = 0", (user_id,))
                lists = cur.fetchall()
                old_covers = [row["cover_file"] for row in lists if row["cover_file"] == f"user-{row['id']}.svg"]
                removed = {"playlists": len(lists)}
                # playlist_tracks y device_playlist_tracks se borran en cascada con la lista.
                cur.execute("DELETE FROM playlists WHERE owner_id = %s AND editorial = 0", (user_id,))
                cur.execute("DELETE FROM user_likes WHERE user_id = %s", (user_id,))
                removed["likes"] = cur.rowcount
                cur.execute("DELETE FROM user_device_likes WHERE user_id = %s", (user_id,))
                removed["device_likes"] = cur.rowcount
                cur.execute("DELETE FROM play_history WHERE user_id = %s", (user_id,))
                removed["history"] = cur.rowcount

                seed = _pick_seed(cur)
                accent_a, accent_b = playlistedit.PALETTE[2]
                cur.execute(
                    """
                    INSERT INTO playlists (name, description, editorial, accent_a, accent_b, cover_file, owner_id)
                    VALUES (%s, %s, 0, %s, %s, 'pending.svg', %s)
                    """,
                    (SEED_PLAYLIST, SEED_DESCRIPTION, accent_a, accent_b, user_id),
                )
                playlist_id = cur.lastrowid
                new_cover = f"user-{playlist_id}.svg"
                cur.execute("UPDATE playlists SET cover_file = %s WHERE id = %s AND owner_id = %s", (new_cover, playlist_id, user_id))
                for position, track_id in enumerate(seed):
                    cur.execute(
                        "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (%s, %s, %s)",
                        (playlist_id, track_id, position),
                    )
                likes = seed[:SEED_LIKES]
                for offset, track_id in enumerate(likes):
                    cur.execute(
                        "INSERT INTO user_likes (user_id, track_id, liked_at) VALUES (%s, %s, NOW() - INTERVAL %s MINUTE)",
                        (user_id, track_id, offset),
                    )
                # Historial de muestra (no suma reproducciones a las pistas).
                history = seed[:SEED_HISTORY]
                for offset, track_id in enumerate(history):
                    cur.execute(
                        "INSERT INTO play_history (track_id, user_id, played_at) VALUES (%s, %s, NOW() - INTERVAL %s MINUTE)",
                        (track_id, user_id, 10 * (offset + 1)),
                    )
                cur.execute(
                    """
                    INSERT INTO app_meta (meta_key, meta_value) VALUES ('demo_reset_at', NOW())
                    ON DUPLICATE KEY UPDATE meta_value = NOW()
                    """
                )
            playlistedit.write_playlist_cover(new_cover, accent_a, accent_b, SEED_PLAYLIST)
            conn.commit()
        except BaseException:
            conn.rollback()
            if new_cover:
                _remove_covers([new_cover])
            raise
        _remove_covers(old_covers)
        return {
            "user_id": user_id,
            "created": created,
            "password_changed": password_changed,
            "removed": removed,
            "seeded": {"playlist_id": playlist_id, "tracks": len(seed), "likes": len(likes), "history": len(history)},
        }
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK('spotyofo_demo_reset')")


def _remove_covers(names):
    for name in names:
        if not name or "/" in name or "\\" in name or not name.startswith("user-") or not name.endswith(".svg"):
            continue
        try:
            (playlistedit.COVERS_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass
