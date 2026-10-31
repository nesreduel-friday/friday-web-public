"""Ordnet Mediendateien aus memories/ den Einträgen aus memories_history.json zu.

Dateinamen im Export sehen so aus: 2023-07-29_<uuid>-main.jpg / -overlay.png.
Zugeordnet wird über Tag (UTC) und Medientyp. Gibt es an einem Tag mehrere
Dateien desselben Typs, ist die Zuordnung nicht eindeutig und wird nicht geraten.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .export import Memory

STATUS_OK = "zugeordnet"
STATUS_AMBIGUOUS = "mehrdeutig"
STATUS_NO_METADATA = "keine Metadaten"
STATUS_UNASSIGNED = "nicht zugeordnet"

_FILE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_([0-9a-fA-F-]+)-(main|overlay)\.(\w+)$")
_VIDEO_EXTENSIONS = {"mp4", "mov"}


@dataclass
class Result:
    # Dateiname (im Export) -> (Status, zugeordneter Eintrag oder None)
    files: dict[str, tuple[str, Memory | None]] = field(default_factory=dict)
    # Mehrdeutige Dateien -> mögliche Einträge (gleicher Tag und Typ)
    candidates: dict[str, list[Memory]] = field(default_factory=dict)
    # Einträge, zu denen keine Datei gefunden wurde
    unassigned: list[Memory] = field(default_factory=list)


def match(filenames: list[str], memories: list[Memory]) -> Result:
    result = Result()
    main_by_uuid: dict[str, tuple[str, Memory | None]] = {}
    candidates: dict[tuple[str, str], list[str]] = defaultdict(list)
    overlays: list[tuple[str, str]] = []  # (Dateiname, uuid)

    for name in filenames:
        found = _FILE_NAME.match(PurePosixPath(name).name)
        if not found:
            result.files[name] = (STATUS_NO_METADATA, None)
            continue
        day, uuid, variant, extension = found.groups()
        if variant == "overlay":
            overlays.append((name, uuid))
            continue
        kind = "video" if extension.lower() in _VIDEO_EXTENSIONS else "image"
        candidates[(day, kind)].append(name)

    memories_by_key: dict[tuple[str, str], list[Memory]] = defaultdict(list)
    for memory in memories:
        memories_by_key[(memory.day, memory.kind)].append(memory)

    assigned_memories: set[int] = set()
    for key, names in candidates.items():
        entries = memories_by_key.get(key, [])
        if len(names) == 1 and len(entries) == 1:
            result.files[names[0]] = (STATUS_OK, entries[0])
            assigned_memories.add(id(entries[0]))
            uuid = _uuid_of(names[0])
            main_by_uuid[uuid] = result.files[names[0]]
        elif entries:
            for name in names:
                result.files[name] = (STATUS_AMBIGUOUS, None)
                result.candidates[name] = entries
                main_by_uuid[_uuid_of(name)] = (STATUS_AMBIGUOUS, None)
        else:
            for name in names:
                result.files[name] = (STATUS_NO_METADATA, None)
                main_by_uuid[_uuid_of(name)] = (STATUS_NO_METADATA, None)

    for name, uuid in overlays:
        status, memory = main_by_uuid.get(uuid, (STATUS_NO_METADATA, None))
        result.files[name] = (status, memory)

    result.unassigned = [m for m in memories if id(m) not in assigned_memories]
    return result


def _uuid_of(name: str) -> str:
    return _FILE_NAME.match(PurePosixPath(name).name).group(2)
