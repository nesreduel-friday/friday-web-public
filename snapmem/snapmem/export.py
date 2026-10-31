"""Liest memories_history.json aus dem Snapchat-Export."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
_LOCATION = re.compile(r"Latitude,\s*Longitude:\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Memory:
    taken: datetime  # UTC
    media_type: str  # "Image" oder "Video"
    lat: float | None
    lon: float | None

    @property
    def day(self) -> str:
        return self.taken.strftime("%Y-%m-%d")

    @property
    def kind(self) -> str:
        return "video" if self.media_type == "Video" else "image"


def parse_memories(raw: bytes) -> list[Memory]:
    data = json.loads(raw.decode("utf-8-sig"))
    return [_parse_item(item) for item in data["Saved Media"]]


def _parse_item(item: dict) -> Memory:
    taken = datetime.strptime(item["Date"], _DATE_FORMAT).replace(tzinfo=timezone.utc)
    found = _LOCATION.search(item.get("Location") or "")
    lat, lon = (float(found.group(1)), float(found.group(2))) if found else (None, None)
    return Memory(taken=taken, media_type=item["Media Type"], lat=lat, lon=lon)


def parse_chat_ids(raw: bytes) -> dict[str, datetime]:
    """Ordnet Media-IDs aus chat_history.json dem Zeitpunkt der Nachricht zu."""
    data = json.loads(raw.decode("utf-8-sig"))
    rows = [r for conversation in data.values() for r in conversation] if isinstance(data, dict) else data
    found = {}
    for row in rows:
        ids = row.get("Media IDs") or ""
        if not ids:
            continue
        taken = datetime.strptime(row["Created"], _DATE_FORMAT).replace(tzinfo=timezone.utc)
        for media_id in ids.split(","):
            if media_id.strip():
                found[media_id.strip()] = taken
    return found


@dataclass(frozen=True)
class ChatInfo:
    """ids: Media-ID → (Zeitpunkt, Chatname). tage: Tag → Chats mit irgendeiner Nachricht an dem Tag."""
    ids: dict[str, tuple[datetime, str]]
    tage: dict[str, list[str]]


def parse_chat_details(raw: bytes) -> ChatInfo:
    """Wie parse_chat_ids, nennt aber auch den Chat. Der Export ordnet nur wenige Medien über ihre ID zu;
    für alle anderen gibt es nur die Chats, die an dem Tag aktiv waren (kein Beweis, nur ein Hinweis)."""
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        return ChatInfo({k: (t, "") for k, t in parse_chat_ids(raw).items()}, {})
    ids: dict[str, tuple[datetime, str]] = {}
    tage: dict[str, set[str]] = {}
    for key, rows in data.items():
        name = next((r.get("Conversation Title") for r in rows if r.get("Conversation Title")), None) or key
        for row in rows:
            tage.setdefault(row["Created"][:10], set()).add(name)
            for media_id in (row.get("Media IDs") or "").split(","):
                if media_id.strip():
                    taken = datetime.strptime(row["Created"], _DATE_FORMAT).replace(tzinfo=timezone.utc)
                    ids[media_id.strip()] = (taken, name)
    return ChatInfo(ids, {t: sorted(n) for t, n in tage.items()})
