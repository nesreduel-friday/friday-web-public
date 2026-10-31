"""Zip rein, Zip raus: Memories und Chat-Medien mit Metadaten-CSV verpacken."""

import argparse
import csv
import io
import sys
import zipfile
from collections import Counter
from pathlib import PurePosixPath

import piexif
from mutagen import MutagenError

from .chat import STATUS_CHAT_DATE, STATUS_CHAT_OK, chat_name, match_chat
from .export import parse_chat_ids, parse_memories
from .faces import count_faces
from .matching import STATUS_OK, match
from .metadata import JPEG_SUFFIXES, VIDEO_SUFFIXES, write_jpeg, write_mp4

JSON_NAME = "json/memories_history.json"
CHAT_JSON_NAME = "json/chat_history.json"
METADATA_NAME = "memories_metadaten.csv"
PEOPLE_DIR = "mit_personen"
IMAGE_SUFFIXES = JPEG_SUFFIXES | {".png", ".webp"}
STATUS_WITH_TIME = {STATUS_OK, STATUS_CHAT_OK}


def memories_name(name: str) -> str | None:
    """Gibt 'memories/<datei>' zurück, wenn die Datei direkt im memories-Ordner liegt."""
    parts = PurePosixPath(name).parts
    if len(parts) >= 2 and parts[-2] == "memories":
        return f"memories/{parts[-1]}"
    return None


def process(input_path: str, output_path: str) -> Counter:
    with zipfile.ZipFile(input_path) as src:
        json_names = [n for n in src.namelist() if n.endswith(JSON_NAME)]
        if not json_names:
            raise SystemExit(f"{JSON_NAME} nicht im Zip gefunden")
        memories = parse_memories(src.read(json_names[0]))

        chat_json = [n for n in src.namelist() if n.endswith(CHAT_JSON_NAME)]
        chat_ids = parse_chat_ids(src.read(chat_json[0])) if chat_json else {}

        sources = {}
        for info in src.infolist():
            if info.is_dir():
                continue
            target = memories_name(info.filename) or chat_name(info.filename)
            if target:
                sources[target] = info.filename

        memory_targets = sorted(t for t in sources if t.startswith("memories/"))
        chat_targets = sorted(t for t in sources if t.startswith("chat_media/"))
        result = match(memory_targets, memories)
        files = dict(result.files)
        files.update(match_chat(chat_targets, chat_ids))

        failed = 0
        faces = {}
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as dst:
            for target, source in sorted(sources.items()):
                data = src.read(source)
                status, memory = files.get(target, (None, None))
                if status in STATUS_WITH_TIME and memory is not None:
                    data, ok = _with_metadata(target, data, memory)
                    failed += not ok
                if _is_photo(target):
                    faces[target] = count_faces(data)
                dst.writestr(target, data)
                if faces.get(target):
                    dst.writestr(f"{PEOPLE_DIR}/{PurePosixPath(target).name}", data)
            dst.writestr(METADATA_NAME, _csv(result, files, faces).encode("utf-8-sig"))

    counts = Counter(status for status, _ in files.values())
    counts["mit Gesicht"] = sum(1 for n in faces.values() if n)
    if failed:
        counts["metadaten fehlgeschlagen"] = failed
    return counts


def _is_photo(target: str) -> bool:
    name = PurePosixPath(target).name.lower()
    suffix = PurePosixPath(target).suffix.lower()
    return suffix in IMAGE_SUFFIXES and "overlay" not in name and "thumbnail" not in name


def _with_metadata(target: str, data: bytes, memory) -> tuple[bytes, bool]:
    suffix = PurePosixPath(target).suffix.lower()
    try:
        if suffix in JPEG_SUFFIXES:
            return write_jpeg(data, memory.taken, memory.lat, memory.lon), True
        if suffix in VIDEO_SUFFIXES:
            return write_mp4(data, memory.taken, memory.lat, memory.lon), True
    except (piexif.InvalidImageDataError, ValueError, OSError, MutagenError) as error:
        print(f"Metadaten nicht geschrieben: {target} ({error})", file=sys.stderr)
        return data, False
    return data, True


def _csv(result, files, faces) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "datei", "status", "aufnahme_utc", "medientyp", "breitengrad", "laengengrad",
        "quelle", "gesichter", "kandidaten",
    ])
    for name, (status, memory) in sorted(files.items()):
        candidates = " | ".join(_describe(m) for m in result.candidates.get(name, []))
        writer.writerow(_row(name, status, memory, faces.get(name), candidates))
    for memory in result.unassigned:
        writer.writerow(_row("", "nicht zugeordnet", memory, None, ""))
    return buffer.getvalue()


def _describe(memory) -> str:
    place = "" if memory.lat is None else f" {memory.lat},{memory.lon}"
    return f"{memory.taken.strftime('%H:%M:%S')}{place}"


def _row(name: str, status: str, memory, face_count, candidates: str) -> list:
    source = "chat" if name.startswith("chat_media/") else "memories"
    faces = "" if face_count is None else face_count
    if memory is None:
        return [name, status, "", "", "", "", source, faces, candidates]
    if status == STATUS_CHAT_DATE:
        taken = memory.taken.strftime("%Y-%m-%d")
    else:
        taken = memory.taken.strftime("%Y-%m-%d %H:%M:%S")
    return [
        name,
        status,
        taken,
        memory.media_type,
        "" if memory.lat is None else memory.lat,
        "" if memory.lon is None else memory.lon,
        source,
        faces,
        candidates,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snapchat-Memories und Chat-Medien verpacken")
    parser.add_argument("input", help="Snapchat-Export als .zip")
    parser.add_argument("output", help="Ziel-.zip")
    args = parser.parse_args(argv)

    counts = process(args.input, args.output)
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
