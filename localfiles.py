"""Lectura de la carpeta Música de este equipo. No copia los archivos."""

import os
import re
from pathlib import Path
from urllib.parse import quote

try:
    import winreg
except ImportError:
    winreg = None

import mutagen

from songs import song_key

AUDIO_EXT = {".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
COVER_NAMES = ("folder.jpg", "folder.jpeg", "cover.jpg", "cover.png", "front.jpg", "album.jpg", "albumart.jpg")
MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_TAG_CACHE = {}


class LocalFileError(Exception):
    def __init__(self, message, code=404):
        super().__init__(message)
        self.message = message
        self.code = code


def music_root():
    override = os.environ.get("SPOTY_MUSIC_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "My Music")
                folder = Path(value)
                if folder.is_dir():
                    return folder.resolve()
        except OSError:
            pass
    for name in ("Music", "Música", "Musica"):
        folder = Path.home() / name
        if folder.is_dir():
            return folder.resolve()
    return (Path.home() / "Music").resolve()


def _sort_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def resolve_inside(relative, allowed_ext):
    root = music_root()
    if not root.is_dir():
        raise LocalFileError("No encontré la carpeta Música de este equipo.")
    raw = (relative or "").replace("\\", "/").strip().strip("/")
    if raw.startswith("/") or re.match(r"^[a-zA-Z]:", raw):
        raise LocalFileError("Ruta no permitida.", 400)
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise LocalFileError("Ruta no permitida.", 400)
    candidate = root.joinpath(*parts).resolve() if parts else root
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LocalFileError("Ruta no permitida.", 400) from exc
    if not candidate.exists():
        raise LocalFileError("No está en la carpeta Música.")
    if candidate.is_file() and candidate.suffix.lower() not in allowed_ext:
        raise LocalFileError("Ese archivo no se puede abrir desde Archivo de Equipo.", 415)
    return root, candidate


def relative_to_root(root, path):
    return path.resolve().relative_to(root).as_posix()


def _cover_file(folder):
    for name in COVER_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _tags(path, folder_name):
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _TAG_CACHE.get(key)
    if cached:
        return cached
    title = path.stem
    artist = "Archivo de equipo"
    album = folder_name or "Música"
    duration_ms = 0
    track_number = 0
    try:
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            title = (audio.get("title") or [title])[0] or title
            artist = (audio.get("artist") or [artist])[0] or artist
            album = (audio.get("album") or [album])[0] or album
            length = getattr(audio.info, "length", 0) or 0
            duration_ms = int(length * 1000)
            raw_number = str((audio.get("tracknumber") or ["0"])[0]).split("/", 1)[0].strip()
            track_number = int(raw_number) if raw_number.isdigit() else 0
    except Exception:
        pass
    result = {
        "title": str(title).strip() or path.stem,
        "artist": str(artist).strip() or "Archivo de equipo",
        "album": str(album).strip() or folder_name or "Música",
        "duration_ms": duration_ms,
        "track_number": track_number,
    }
    if len(_TAG_CACHE) > 4000:
        _TAG_CACHE.clear()
    _TAG_CACHE[key] = result
    return result


def _public_track(root, entry, folder, cover_url, cover_path, rel_folder):
    meta = _tags(entry, folder.name if folder != root else "Música")
    rel_file = relative_to_root(root, entry)
    return {
        "id": rel_file,
        "title": meta["title"],
        "artist": meta["artist"],
        "artist_id": "",
        "album": meta["album"],
        "album_id": "",
        "duration_ms": meta["duration_ms"],
        "cover_url": cover_url,
        "cover_path": cover_path,
        "audio_url": f"/api/equipo/audio?path={_quote(rel_file)}",
        "source": "device",
        "liked": False,
        "bpm": "",
        "musical_key": "",
        "genre": "Archivo de equipo",
        "folder": rel_folder,
        "track_number": meta["track_number"],
        "song_key": song_key(meta["title"], meta["artist"], meta["duration_ms"]),
    }


def collect_tracks(relative=""):
    """Pistas de un archivo o de un solo nivel de carpeta. No entra en subcarpetas."""
    root, target = resolve_inside(relative or "", AUDIO_EXT | IMAGE_EXT)
    if target.is_file():
        folder = target.parent
        rel_folder = "" if folder == root else relative_to_root(root, folder)
        cover = _cover_file(folder)
        cover_path = relative_to_root(root, cover) if cover else ""
        cover_url = f"/api/equipo/image?path={_quote(cover_path)}" if cover_path else "/static/covers/equipo.svg"
        return [_public_track(root, target, folder, cover_url, cover_path, rel_folder)]
    return list_directory(relative or "")["tracks"]


def saved_track(row, liked=False):
    cover = row.get("cover_rel") or ""
    rel = row["rel_path"]
    return {
        "id": rel,
        "title": row["title"],
        "artist": row["artist"],
        "artist_id": "",
        "album": row["album"],
        "album_id": "",
        "duration_ms": int(row["duration_ms"] or 0),
        "cover_url": f"/api/equipo/image?path={_quote(cover)}" if cover else "/static/covers/equipo.svg",
        "audio_url": f"/api/equipo/audio?path={_quote(rel)}",
        "source": "device",
        "liked": bool(liked),
        "bpm": "",
        "musical_key": "",
        "genre": "Archivo de equipo",
        "folder": row.get("folder_rel") or "",
        "track_number": int(row.get("track_number") or 0),
        "song_key": song_key(row["title"], row["artist"], row["duration_ms"]),
    }


def list_directory(relative=""):
    root, folder = resolve_inside(relative, AUDIO_EXT | IMAGE_EXT)
    if not folder.is_dir():
        raise LocalFileError("Eso es un archivo, no una carpeta.", 400)
    rel = "" if folder == root else relative_to_root(root, folder)
    parent = None
    if folder != root:
        parent_path = folder.parent
        parent = "" if parent_path == root else relative_to_root(root, parent_path)
    cover = _cover_file(folder)
    cover_path = relative_to_root(root, cover) if cover else ""
    cover_url = f"/api/equipo/image?path={_quote(cover_path)}" if cover_path else "/static/covers/equipo.svg"
    folders = []
    tracks = []
    try:
        entries = list(folder.iterdir())
    except OSError as exc:
        raise LocalFileError("No se pudo leer esa carpeta.") from exc
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                audio_count = 0
                folder_count = 0
                try:
                    for child in entry.iterdir():
                        if child.name.startswith("."):
                            continue
                        if child.is_dir():
                            folder_count += 1
                        elif child.suffix.lower() in AUDIO_EXT:
                            audio_count += 1
                except OSError:
                    pass
                folders.append({
                    "name": entry.name,
                    "path": relative_to_root(root, entry),
                    "audio_count": audio_count,
                    "folder_count": folder_count,
                })
            elif entry.is_file() and entry.suffix.lower() in AUDIO_EXT:
                tracks.append(_public_track(root, entry, folder, cover_url, cover_path, rel))
        except OSError:
            continue
    folders.sort(key=lambda item: _sort_key(item["name"]))
    tracks.sort(key=lambda item: (item["track_number"] or 10**6, _sort_key(Path(item["id"]).name)))
    crumbs = []
    walking = Path()
    if rel:
        for part in Path(rel).parts:
            walking = walking / part
            crumbs.append({"name": part, "path": walking.as_posix()})
    return {
        "name": "Música" if folder == root else folder.name,
        "path": rel,
        "parent": parent,
        "root_path": str(root),
        "location": str(folder),
        "cover_url": cover_url,
        "crumbs": crumbs,
        "folders": folders,
        "tracks": tracks,
    }


def _quote(value):
    return quote(value, safe="")


def open_audio(relative):
    _root, path = resolve_inside(relative, AUDIO_EXT)
    if not path.is_file():
        raise LocalFileError("No está ese audio.")
    return path, MIME.get(path.suffix.lower(), "application/octet-stream")


def open_image(relative):
    _root, path = resolve_inside(relative, IMAGE_EXT)
    if not path.is_file():
        raise LocalFileError("No está esa imagen.")
    return path, MIME.get(path.suffix.lower(), "application/octet-stream")
