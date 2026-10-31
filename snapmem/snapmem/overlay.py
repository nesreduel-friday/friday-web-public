"""Overlay (Text, Sticker, Geo-Filter) wieder in Bild und Video einbrennen.

Snapchat liefert `…-main.jpg|mp4` und `…-overlay.png` getrennt. Das Overlay ist ein
PNG mit Alphakanal in der Größe des Hauptbilds (bei Videos oft kleiner, es wird skaliert).
"""

import io
import subprocess
from pathlib import Path

from PIL import Image, UnidentifiedImageError

JPEG_QUALITY = 95


def merge_image(main: bytes, overlay: bytes | None) -> bytes:
    """Gibt das Hauptbild mit eingebranntem Overlay als JPEG zurück (EXIF bleibt erhalten).

    Ohne Overlay kommt das Hauptbild unverändert zurück. Ein unlesbares Overlay ist ein ValueError.
    """
    if overlay is None:
        return main
    try:
        top = Image.open(io.BytesIO(overlay)).convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Overlay nicht lesbar: {exc}") from None
    base = Image.open(io.BytesIO(main))
    exif = base.info.get("exif", b"")
    base = base.convert("RGBA")
    if top.size != base.size:
        top = top.resize(base.size, Image.LANCZOS)
    merged = Image.alpha_composite(base, top).convert("RGB")
    out = io.BytesIO()
    merged.save(out, "JPEG", quality=JPEG_QUALITY, exif=exif)
    return out.getvalue()


def merge_video(main: Path, overlay: Path, out: Path, timeout: int = 900) -> None:
    """Brennt das Overlay mit ffmpeg ins Video; Ton wird kopiert. Fehler → RuntimeError."""
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(main), "-i", str(overlay),
        "-filter_complex", "[1:v][0:v]scale2ref[ov][bg];[bg][ov]overlay=0:0:format=auto,format=yuv420p[v]",
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg: Zeitlimit überschritten") from None
    if done.returncode != 0:
        raise RuntimeError(f"ffmpeg: {done.stderr.strip()[-300:]}")
