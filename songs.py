"""Identidad de una canción para no repetirla en Me gusta."""

import re
import unicodedata


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def song_key(title, artist, duration_ms):
    """Misma obra: título, artista y duración en una ventana de 2 segundos."""
    seconds = int(round(max(0, int(duration_ms or 0)) / 1000))
    bucket = seconds // 2
    return f"{_norm(title)}|{_norm(artist)}|{bucket}"
