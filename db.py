"""Esquema y acceso a MariaDB para el catálogo local."""

import threading
from datetime import datetime
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor

from config import DB_CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
  id INT AUTO_INCREMENT PRIMARY KEY,
  slug VARCHAR(80) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  genre VARCHAR(80) NOT NULL,
  origin VARCHAR(80) NOT NULL,
  bio VARCHAR(500) NOT NULL,
  accent_a CHAR(7) NOT NULL,
  accent_b CHAR(7) NOT NULL,
  cover_file VARCHAR(160) NOT NULL,
  source ENUM('catalog','user') NOT NULL DEFAULT 'catalog'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS albums (
  id INT AUTO_INCREMENT PRIMARY KEY,
  slug VARCHAR(80) NOT NULL UNIQUE,
  artist_id INT NOT NULL,
  title VARCHAR(160) NOT NULL,
  year SMALLINT NOT NULL,
  credit VARCHAR(180) NOT NULL,
  accent_a CHAR(7) NOT NULL,
  accent_b CHAR(7) NOT NULL,
  cover_file VARCHAR(160) NOT NULL,
  source ENUM('catalog','user') NOT NULL DEFAULT 'catalog',
  CONSTRAINT fk_albums_artist FOREIGN KEY (artist_id) REFERENCES artists(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tracks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  slug VARCHAR(80) NOT NULL UNIQUE,
  album_id INT NOT NULL,
  artist_id INT NOT NULL,
  title VARCHAR(160) NOT NULL,
  track_number SMALLINT NOT NULL,
  duration_ms INT NOT NULL,
  audio_file VARCHAR(160) NOT NULL,
  genre VARCHAR(80) NOT NULL,
  bpm SMALLINT NOT NULL,
  musical_key VARCHAR(32) NOT NULL,
  plays INT NOT NULL DEFAULT 0,
  source ENUM('catalog','user') NOT NULL DEFAULT 'catalog',
  file_hash CHAR(64) NULL,
  original_name VARCHAR(255) NULL,
  file_size INT UNSIGNED NULL,
  uploaded_at TIMESTAMP NULL DEFAULT NULL,
  UNIQUE KEY uq_tracks_hash (file_hash),
  KEY idx_tracks_source (source),
  KEY idx_tracks_genre (genre),
  KEY idx_tracks_plays (plays),
  CONSTRAINT fk_tracks_album FOREIGN KEY (album_id) REFERENCES albums(id),
  CONSTRAINT fk_tracks_artist FOREIGN KEY (artist_id) REFERENCES artists(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playlists (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  description VARCHAR(280) NOT NULL DEFAULT '',
  editorial TINYINT(1) NOT NULL DEFAULT 0,
  accent_a CHAR(7) NOT NULL,
  accent_b CHAR(7) NOT NULL,
  cover_file VARCHAR(160) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id INT NOT NULL,
  track_id INT NOT NULL,
  position INT NOT NULL,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (playlist_id, track_id),
  CONSTRAINT fk_pt_playlist FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
  CONSTRAINT fk_pt_track FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS liked_tracks (
  track_id INT NOT NULL PRIMARY KEY,
  liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_likes_track FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS play_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  track_id INT NOT NULL,
  played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_history_played (played_at),
  KEY idx_history_track (track_id),
  CONSTRAINT fk_history_track FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS device_tracks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  rel_path VARCHAR(512) NOT NULL,
  title VARCHAR(180) NOT NULL,
  artist VARCHAR(180) NOT NULL,
  album VARCHAR(180) NOT NULL,
  duration_ms INT NOT NULL DEFAULT 0,
  track_number SMALLINT NOT NULL DEFAULT 0,
  cover_rel VARCHAR(512) NULL,
  folder_rel VARCHAR(512) NOT NULL DEFAULT '',
  saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_device_path (rel_path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS device_likes (
  track_id INT NOT NULL PRIMARY KEY,
  liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_device_likes FOREIGN KEY (track_id) REFERENCES device_tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(32) NOT NULL,
  display_name VARCHAR(60) NOT NULL DEFAULT '',
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','user') NOT NULL DEFAULT 'user',
  status ENUM('pending','active','rejected','blocked') NOT NULL DEFAULT 'pending',
  must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  session_version INT NOT NULL DEFAULT 1,
  failed_logins INT NOT NULL DEFAULT 0,
  locked_until DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  approved_at DATETIME NULL,
  approved_by INT NULL,
  last_login_at DATETIME NULL,
  UNIQUE KEY uq_users_username (username),
  KEY idx_users_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_likes (
  user_id INT NOT NULL,
  track_id INT NOT NULL,
  liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, track_id),
  KEY idx_user_likes_track (track_id),
  CONSTRAINT fk_user_likes_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_user_likes_track FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_device_likes (
  user_id INT NOT NULL,
  track_id INT NOT NULL,
  liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, track_id),
  KEY idx_user_device_likes_track (track_id),
  CONSTRAINT fk_udl_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_udl_track FOREIGN KEY (track_id) REFERENCES device_tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_meta (
  meta_key VARCHAR(64) NOT NULL PRIMARY KEY,
  meta_value VARCHAR(255) NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS device_playlist_tracks (
  playlist_id INT NOT NULL,
  track_id INT NOT NULL,
  position INT NOT NULL,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (playlist_id, track_id),
  CONSTRAINT fk_dpt_playlist FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
  CONSTRAINT fk_dpt_track FOREIGN KEY (track_id) REFERENCES device_tracks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# El primer parámetro de TRACK_SQL es siempre el id del usuario (sus Me gusta).
TRACK_SQL = """
SELECT
  t.id, t.slug, t.title, t.track_number, t.duration_ms, t.audio_file, t.genre,
  t.bpm, t.musical_key, t.plays, t.album_id, t.artist_id, t.source,
  a.name AS artist, a.slug AS artist_slug,
  al.title AS album, al.slug AS album_slug, al.cover_file AS cover, al.year AS year,
  al.accent_a, al.accent_b,
  IF(l.track_id IS NULL, 0, 1) AS liked
FROM tracks t
JOIN artists a ON a.id = t.artist_id
JOIN albums al ON al.id = t.album_id
LEFT JOIN user_likes l ON l.track_id = t.id AND l.user_id = %s
"""

# Columnas agregadas después de la primera versión (subidas propias).
MIGRATION_COLUMNS = [
    ("artists", "source", "ALTER TABLE artists ADD COLUMN source ENUM('catalog','user') NOT NULL DEFAULT 'catalog'"),
    ("albums", "source", "ALTER TABLE albums ADD COLUMN source ENUM('catalog','user') NOT NULL DEFAULT 'catalog'"),
    ("tracks", "source", "ALTER TABLE tracks ADD COLUMN source ENUM('catalog','user') NOT NULL DEFAULT 'catalog'"),
    ("tracks", "file_hash", "ALTER TABLE tracks ADD COLUMN file_hash CHAR(64) NULL"),
    ("tracks", "original_name", "ALTER TABLE tracks ADD COLUMN original_name VARCHAR(255) NULL"),
    ("tracks", "file_size", "ALTER TABLE tracks ADD COLUMN file_size INT UNSIGNED NULL"),
    ("tracks", "uploaded_at", "ALTER TABLE tracks ADD COLUMN uploaded_at TIMESTAMP NULL DEFAULT NULL"),
    # Cuentas: dueño de cada lista propia e historial por usuario (NULL = datos anteriores a las cuentas).
    ("playlists", "owner_id", "ALTER TABLE playlists ADD COLUMN owner_id INT NULL DEFAULT NULL"),
    ("play_history", "user_id", "ALTER TABLE play_history ADD COLUMN user_id INT NULL DEFAULT NULL"),
]
MIGRATION_INDEXES = [
    ("tracks", "uq_tracks_hash", "ALTER TABLE tracks ADD UNIQUE KEY uq_tracks_hash (file_hash)"),
    ("tracks", "idx_tracks_source", "ALTER TABLE tracks ADD KEY idx_tracks_source (source)"),
    ("playlists", "idx_playlists_owner", "ALTER TABLE playlists ADD KEY idx_playlists_owner (owner_id)"),
    ("play_history", "idx_history_user", "ALTER TABLE play_history ADD KEY idx_history_user (user_id, played_at)"),
]
_migrate_lock = threading.Lock()
_migrated = False


def connect():
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset=DB_CONFIG["charset"],
        cursorclass=DictCursor,
        autocommit=True,
    )


def init_schema(conn):
    with conn.cursor() as cur:
        for statement in SCHEMA.split(";"):
            sql = statement.strip()
            if sql:
                cur.execute(sql)
    migrate(conn)


def migrate(conn):
    """Agrega columnas e índices nuevos a una base existente. Idempotente."""
    global _migrated
    if _migrated:
        return
    with _migrate_lock:
        if _migrated:
            return
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
            )
            columns = {(row["TABLE_NAME"], row["COLUMN_NAME"]) for row in cur.fetchall()}
            for table, column, sql in MIGRATION_COLUMNS:
                if (table, column) not in columns:
                    cur.execute(sql)
            cur.execute(
                "SELECT DISTINCT TABLE_NAME, INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()"
            )
            indexes = {(row["TABLE_NAME"], row["INDEX_NAME"]) for row in cur.fetchall()}
            for table, index, sql in MIGRATION_INDEXES:
                if (table, index) not in indexes:
                    cur.execute(sql)
        _migrated = True


def reset_catalog(conn):
    """Borra solo el catálogo editorial. Conserva subidas, listas propias, Me gusta (de cada usuario) e historial.

    Devuelve las referencias de listas propias y Me gusta a pistas del catálogo (por slug)
    para volver a enlazarlas con restore_catalog_refs() después de regenerar.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.playlist_id, t.slug, pt.position, pt.added_at
            FROM playlist_tracks pt
            JOIN playlists p ON p.id = pt.playlist_id
            JOIN tracks t ON t.id = pt.track_id
            WHERE p.editorial = 0 AND t.source = 'catalog'
            """
        )
        playlist_refs = cur.fetchall()
        cur.execute(
            """
            SELECT t.slug, l.liked_at
            FROM liked_tracks l JOIN tracks t ON t.id = l.track_id
            WHERE t.source = 'catalog'
            """
        )
        like_refs = cur.fetchall()
        cur.execute(
            """
            SELECT l.user_id, t.slug, l.liked_at
            FROM user_likes l JOIN tracks t ON t.id = l.track_id
            WHERE t.source = 'catalog'
            """
        )
        user_like_refs = cur.fetchall()
        cur.execute("DELETE FROM playlists WHERE editorial = 1")
        cur.execute("DELETE FROM tracks WHERE source = 'catalog'")
        cur.execute("DELETE FROM albums WHERE source = 'catalog'")
        cur.execute("DELETE FROM artists WHERE source = 'catalog'")
    return {"playlists": playlist_refs, "likes": like_refs, "user_likes": user_like_refs}


def restore_catalog_refs(conn, saved):
    if not saved:
        return
    with conn.cursor() as cur:
        for ref in saved.get("playlists", []):
            cur.execute(
                """
                INSERT IGNORE INTO playlist_tracks (playlist_id, track_id, position, added_at)
                SELECT %s, id, %s, %s FROM tracks WHERE slug = %s AND source = 'catalog'
                """,
                (ref["playlist_id"], ref["position"], ref["added_at"], ref["slug"]),
            )
        for ref in saved.get("likes", []):
            cur.execute(
                """
                INSERT IGNORE INTO liked_tracks (track_id, liked_at)
                SELECT id, %s FROM tracks WHERE slug = %s AND source = 'catalog'
                """,
                (ref["liked_at"], ref["slug"]),
            )
        for ref in saved.get("user_likes", []):
            cur.execute(
                """
                INSERT IGNORE INTO user_likes (user_id, track_id, liked_at)
                SELECT %s, id, %s FROM tracks WHERE slug = %s AND source = 'catalog'
                """,
                (ref["user_id"], ref["liked_at"], ref["slug"]),
            )


def clean(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, Decimal):
        return int(value)
    return value


def row_dict(row):
    if row is None:
        return None
    return {key: clean(val) for key, val in row.items()}
