"""Ordnet die Dateien aus chat_media/ ein.

Hauptdateien heißen 2026-07-06_b~<Media-ID>.jpg (oder _media~<UUID>.mp4).
Die Media-ID steht in chat_history.json.
Passt die ID, gibt es die genaue Uhrzeit. Sonst bleibt nur das Datum aus dem Dateinamen.
Overlay- und Vorschaudateien werden unverändert übernommen.
"""

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .export import Memory

STATUS_CHAT_OK = "chat zugeordnet"
STATUS_CHAT_DATE = "chat nur Datum"
STATUS_EXTRA = "Vorschau/Overlay"

_MEDIA = re.compile(r"^(\d{4}-\d{2}-\d{2})_((?:b|media)~.+)\.(\w+)$")
_VIDEO_EXTENSIONS = {"mp4", "mov"}


def chat_name(name: str) -> str | None:
    """Gibt 'chat_media/<datei>' zurück, wenn die Datei direkt im chat_media-Ordner liegt."""
    parts = PurePosixPath(name).parts
    if len(parts) >= 2 and parts[-2] == "chat_media":
        return f"chat_media/{parts[-1]}"
    return None


def match_chat(names: list[str], ids: dict[str, datetime]) -> dict[str, tuple[str, Memory]]:
    result = {}
    for name in names:
        found = _MEDIA.match(PurePosixPath(name).name)
        if not found:
            result[name] = (STATUS_EXTRA, None)
            continue
        day, media_id, extension = found.groups()
        media_type = "Video" if extension.lower() in _VIDEO_EXTENSIONS else "Image"
        taken = ids.get(media_id)
        if taken is not None:
            result[name] = (STATUS_CHAT_OK, Memory(taken=taken, media_type=media_type, lat=None, lon=None))
        else:
            midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            result[name] = (STATUS_CHAT_DATE, Memory(taken=midnight, media_type=media_type, lat=None, lon=None))
    return result
