"""Versiones livianas de audio para descarga offline y ahorro de datos.

Los originales en static/audio/ no se tocan. Las copias van a static/audio/lite/
(formato Opus en .webm ~96 kbps; si falla, AAC en .m4a ~128 kbps para Safari).

Uso:
  python audiolite.py --sample 3          # genera 3 ejemplos y muestra el ahorro
  python audiolite.py --track user/xx.mp3 # una pista
  python audiolite.py --all               # todas (puede tardar)

En producción: SPOTY_LITE_ROOT=/mnt/ssd/spotyofo-lite (symlink static/audio/lite -> ese path).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import ROOT

AUDIO_ROOT = ROOT / "static" / "audio"
# Producción: SPOTY_LITE_ROOT=/mnt/ssd/spotyofo-lite y symlink static/audio/lite -> ese dir
# para servir /static/audio/lite/... sin copiar audio al repo.
_env_lite = (os.environ.get("SPOTY_LITE_ROOT") or "").strip()
LITE_ROOT = Path(_env_lite).expanduser() if _env_lite else (AUDIO_ROOT / "lite")
OPUS_BITRATE = "96k"
AAC_BITRATE = "128k"


class LiteError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def ffmpeg_bin():
    return shutil.which("ffmpeg")


def safe_rel(audio_file: str) -> str:
    rel = (audio_file or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/") or rel.startswith("/"):
        raise LiteError("Ruta de audio inválida.", 400)
    return rel


def source_path(audio_file: str) -> Path:
    """Resuelve el original. En prod, static/audio/user -> /mnt/ssd/... (symlink);
    validamos la ruta logica bajo AUDIO_ROOT y luego resolve() para leer el archivo real.
    """
    rel = safe_rel(audio_file)
    logical = AUDIO_ROOT / rel
    try:
        logical.relative_to(AUDIO_ROOT)
    except ValueError as exc:
        raise LiteError("Ruta de audio invalida.", 400) from exc
    if ".." in logical.parts:
        raise LiteError("Ruta de audio invalida.", 400)
    if not logical.exists():
        raise LiteError("No esta el archivo original.", 404)
    path = logical.resolve()
    if not path.is_file():
        raise LiteError("No esta el archivo original.", 404)
    return path



def lite_candidates(audio_file: str) -> list[tuple[str, Path, str]]:
    """(codec, path, url_suffix) preferidos: webm/opus, luego m4a/aac."""
    rel = safe_rel(audio_file)
    stem = Path(rel).with_suffix("")
    return [
        ("opus", LITE_ROOT / f"{stem}.webm", f"/static/audio/lite/{stem.as_posix()}.webm"),
        ("aac", LITE_ROOT / f"{stem}.m4a", f"/static/audio/lite/{stem.as_posix()}.m4a"),
    ]


def find_lite(audio_file: str) -> dict | None:
    try:
        src = source_path(audio_file)
    except LiteError:
        return None
    for codec, path, url in lite_candidates(audio_file):
        if path.is_file() and path.stat().st_size > 0:
            return {
                "codec": codec,
                "path": path,
                "url": url,
                "size": path.stat().st_size,
                "original_size": src.stat().st_size,
            }
    return None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _run_ffmpeg(args: list[str]) -> None:
    bin_path = ffmpeg_bin()
    if not bin_path:
        raise LiteError("Falta ffmpeg en el PATH.", 500)
    done = subprocess.run(
        [bin_path, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise LiteError((done.stderr or done.stdout or "ffmpeg falló").strip()[:400], 500)


def encode_opus(src: Path, dest: Path) -> None:
    ensure_parent(dest)
    tmp = dest.with_name(dest.name + ".part")
    try:
        _run_ffmpeg([
            "-y",
            "-i", str(src),
            "-vn",
            "-c:a", "libopus",
            "-b:a", OPUS_BITRATE,
            "-vbr", "on",
            "-compression_level", "10",
            "-application", "audio",
            "-f", "webm",
            str(tmp),
        ])
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def encode_aac(src: Path, dest: Path) -> None:
    ensure_parent(dest)
    tmp = dest.with_name(dest.name + ".part")
    try:
        _run_ffmpeg([
            "-y",
            "-i", str(src),
            "-vn",
            "-c:a", "aac",
            "-b:a", AAC_BITRATE,
            "-movflags", "+faststart",
            "-f", "ipod",
            str(tmp),
        ])
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def ensure_lite(audio_file: str, prefer: str = "opus") -> dict:
    existing = find_lite(audio_file)
    if existing:
        return existing
    src = source_path(audio_file)
    errors = []
    order = ["opus", "aac"] if prefer == "opus" else ["aac", "opus"]
    for codec, path, url in lite_candidates(audio_file):
        if codec not in order:
            continue
        try:
            if codec == "opus":
                encode_opus(src, path)
            else:
                encode_aac(src, path)
            return {
                "codec": codec,
                "path": path,
                "url": url,
                "size": path.stat().st_size,
                "original_size": src.stat().st_size,
            }
        except LiteError as exc:
            errors.append(f"{codec}: {exc.message}")
            if path.exists():
                path.unlink(missing_ok=True)
    raise LiteError("No se pudo comprimir (" + "; ".join(errors) + ").", 500)


def attach_lite_fields(track: dict) -> dict:
    """Agrega lite_url / lite_size / lite_codec si la copia existe (sin generar)."""
    audio_file = track.get("audio_file")
    if not audio_file and track.get("audio_url", "").startswith("/static/audio/"):
        audio_file = track["audio_url"][len("/static/audio/") :]
    info = find_lite(audio_file) if audio_file else None
    if info:
        track["lite_url"] = info["url"]
        track["lite_size"] = info["size"]
        track["lite_codec"] = info["codec"]
        track["original_size"] = info["original_size"]
    else:
        track.setdefault("lite_url", None)
        track.setdefault("lite_size", None)
        track.setdefault("lite_codec", None)
    return track


def iter_audio_files(limit: int | None = None):
    files = sorted(
        p for p in AUDIO_ROOT.rglob("*")
        if p.is_file() and "lite" not in p.parts and p.suffix.lower() in {".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus", ".webm"}
    )
    if limit:
        files = files[:limit]
    return files


def report_row(rel: str, info: dict) -> dict:
    orig = info["original_size"]
    lite = info["size"]
    saved = max(0, orig - lite)
    pct = (100.0 * saved / orig) if orig else 0.0
    return {
        "file": rel,
        "codec": info["codec"],
        "original": orig,
        "lite": lite,
        "saved": saved,
        "pct": round(pct, 1),
        "url": info["url"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera versiones livianas de audio para SpotyOFO")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", type=int, metavar="N", help="Comprime N archivos de muestra")
    g.add_argument("--track", metavar="REL", help="Ruta relativa bajo static/audio/")
    g.add_argument("--all", action="store_true", help="Comprime todos los audios")
    parser.add_argument("--prefer", choices=("opus", "aac"), default="opus")
    args = parser.parse_args(argv)

    if not ffmpeg_bin():
        sys.exit("Falta ffmpeg en el PATH.")

    rows = []
    if args.track:
        info = ensure_lite(args.track, prefer=args.prefer)
        rows.append(report_row(safe_rel(args.track), info))
    else:
        limit = None if args.all else max(1, args.sample)
        for path in iter_audio_files(limit):
            rel = path.relative_to(AUDIO_ROOT).as_posix()
            try:
                info = ensure_lite(rel, prefer=args.prefer)
                rows.append(report_row(rel, info))
                print(f"OK  {rel}  {info['codec']}  {info['original_size']} -> {info['size']}")
            except LiteError as exc:
                print(f"FAIL {rel}  {exc.message}")

    if not rows:
        sys.exit("No hubo archivos para comprimir.")

    total_o = sum(r["original"] for r in rows)
    total_l = sum(r["lite"] for r in rows)
    print("---")
    print(f"Archivos: {len(rows)}")
    print(f"Original: {total_o} bytes ({total_o/1048576:.2f} MB)")
    print(f"Lite:     {total_l} bytes ({total_l/1048576:.2f} MB)")
    print(f"Ahorro:   {total_o-total_l} bytes ({100*(total_o-total_l)/total_o:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
