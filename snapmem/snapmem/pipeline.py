"""Mehrere Snapchat-Export-ZIPs lesen und eine Ergebnis-ZIP schreiben.

Modi:
  alles    jedes Memory (Bild, Video) mit eingebranntem Overlay und Metadaten, dazu Chat-Medien
  sauber   nur Bilder ohne Overlay-Datei und ohne eingebrannte Schrift/Aufkleber/Linsen-Effekt (Schätzung, einbrand.py)
  personen wie sauber, aber nur Bilder mit Gesicht oder ganzer Person

Zeit und Ort gibt es nur, wo die Zuordnung eindeutig ist. Mehrdeutige Dateien bekommen nur das
Datum (Mitternacht UTC), nie eine geratene Uhrzeit oder einen geratenen Ort.
"""

import csv
import io
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

import piexif
from mutagen import MutagenError

from .chat import STATUS_CHAT_DATE, STATUS_CHAT_OK, chat_name, match_chat
from .export import Memory, parse_chat_details, parse_memories
from .matching import STATUS_AMBIGUOUS, STATUS_OK, match
from .metadata import JPEG_SUFFIXES, VIDEO_SUFFIXES, write_jpeg, write_mp4
from .overlay import merge_image, merge_video

MODI = ("alles", "sauber", "personen")
JSON_NAME = "json/memories_history.json"
CHAT_JSON_NAME = "json/chat_history.json"
METADATA_NAME = "memories_metadaten.csv"
_MAIN = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_(?P<uuid>[0-9A-Fa-f-]{36})-main\.(?P<ext>\w+)$")

Fortschritt = Callable[[int, int, str], None]


@dataclass
class Quelle:
    zip_pfad: Path
    mitglied: str


def _liste(zip_pfade: list[Path]):
    """Liest alle ZIPs: Dateiquellen (erste gewinnt), Memory-Einträge ohne Dubletten, Chat-IDs."""
    quellen: dict[str, Quelle] = {}
    memories: list[Memory] = []
    gesehen: set[tuple] = set()
    chat_ids: dict[str, datetime] = {}
    chat_von_id: dict[str, str] = {}
    chat_tage: dict[str, set[str]] = {}
    for pfad in zip_pfade:
        with zipfile.ZipFile(pfad) as z:
            namen = z.namelist()
            for n in namen:
                if n.endswith(JSON_NAME):
                    for m in parse_memories(z.read(n)):
                        schluessel = (m.taken, m.media_type, m.lat, m.lon)
                        if schluessel not in gesehen:
                            gesehen.add(schluessel)
                            memories.append(m)
                elif n.endswith(CHAT_JSON_NAME):
                    info = parse_chat_details(z.read(n))
                    for media_id, (taken, chat) in info.ids.items():
                        chat_ids[media_id] = taken
                        chat_von_id[media_id] = chat
                    for tag, chats in info.tage.items():
                        chat_tage.setdefault(tag, set()).update(chats)
            for info in z.infolist():
                if info.is_dir():
                    continue
                teile = PurePosixPath(info.filename).parts
                ziel = None
                if len(teile) >= 2 and teile[-2] == "memories" and not info.filename.endswith(".html"):
                    ziel = f"memories/{teile[-1]}"
                else:
                    ziel = chat_name(info.filename)
                if ziel and ziel not in quellen:
                    quellen[ziel] = Quelle(pfad, info.filename)
    return quellen, memories, chat_ids, chat_von_id, {t: sorted(c) for t, c in chat_tage.items()}


def _tag(name: str) -> datetime | None:
    found = re.match(r"^(?:memories|chat_media)/(\d{4}-\d{2}-\d{2})_", name)
    if not found:
        return None
    return datetime.strptime(found.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _metadaten(name: str, data: bytes, status: str, memory: Memory | None,
               beschreibung: str | None = None) -> tuple[bytes, str]:
    """Schreibt Zeit/Ort, wo eindeutig, sonst nur das Datum. Gibt (Daten, Hinweis) zurück."""
    if memory is not None and status in (STATUS_OK, STATUS_CHAT_OK):
        taken, lat, lon = memory.taken, memory.lat, memory.lon
    else:
        taken, lat, lon = (memory.taken if memory else _tag(name)), None, None
    if taken is None:
        return data, ""
    suffix = PurePosixPath(name).suffix.lower()
    try:
        if suffix in JPEG_SUFFIXES:
            return write_jpeg(data, taken, lat, lon, beschreibung), ""
        if suffix in VIDEO_SUFFIXES:
            return write_mp4(data, taken, lat, lon, beschreibung), ""
    except (piexif.InvalidImageDataError, ValueError, OSError, MutagenError) as exc:
        return data, f"Metadaten nicht geschrieben: {exc}"
    return data, ""


def _ausgabename(name: str) -> str:
    return name.replace("-main.", ".")


def _zeile(datei, quelle, status, memory, extra) -> list:
    taken = ""
    if memory is not None:
        taken = memory.taken.strftime("%Y-%m-%d" if status == STATUS_CHAT_DATE else "%Y-%m-%d %H:%M:%S")
    elif datei:
        tag = _tag(datei)
        taken = tag.strftime("%Y-%m-%d") if tag else ""
    return [
        datei, quelle, status, taken,
        "" if memory is None else memory.media_type,
        "" if memory is None or memory.lat is None else memory.lat,
        "" if memory is None or memory.lon is None else memory.lon,
        extra.get("overlay", ""), extra.get("verschmolzen", ""), extra.get("gesichter", ""),
        extra.get("personen", ""), extra.get("einbrand", ""),
        extra.get("kandidaten", ""), extra.get("chat", ""), extra.get("moegliche_chats", ""), extra.get("hinweis", ""),
    ]


KOPF = ["datei", "quelle", "status", "aufnahme_utc", "medientyp", "breitengrad", "laengengrad",
        "overlay", "verschmolzen", "gesichter", "personen", "einbrand", "kandidaten", "chat", "moegliche_chats", "hinweis"]


def _ja(wert) -> str:
    return "" if wert is None else ("ja" if wert else "nein")


def run(zip_pfade: list[str | Path], modus: str, ziel: str | Path, fortschritt: Fortschritt | None = None,
        pruefer=None) -> Counter:
    if modus not in MODI:
        raise ValueError(f"Unbekannter Modus: {modus}")
    pfade = [Path(p) for p in zip_pfade]
    meldung = fortschritt or (lambda fertig, gesamt, schritt: None)
    quellen, memories, chat_ids, chat_von_id, chat_tage = _liste(pfade)
    if not any(n.startswith("memories/") for n in quellen) and not memories:
        raise ValueError("In den ZIPs wurden keine Memories gefunden.")

    memory_namen = sorted(n for n in quellen if n.startswith("memories/"))
    ergebnis = match(memory_namen, memories)
    dateien = dict(ergebnis.files)
    chat_namen = sorted(n for n in quellen if n.startswith("chat_media/"))
    dateien.update(match_chat(chat_namen, chat_ids))
    hauptdateien = [n for n in sorted(dateien) if n.startswith("chat_media/") or _MAIN.match(PurePosixPath(n).name)]
    if modus != "alles":
        hauptdateien = [n for n in hauptdateien if PurePosixPath(n).suffix.lower() in JPEG_SUFFIXES | {".png", ".webp"}]
    if pruefer is None and modus != "alles":
        from .pruefer import StandardPruefer
        pruefer = StandardPruefer()

    zaehler: Counter = Counter()
    zeilen: list[list] = []
    offene = {p: zipfile.ZipFile(p) for p in pfade}
    arbeit = Path(tempfile.mkdtemp(prefix="snapmem-"))
    try:
        with zipfile.ZipFile(ziel, "w", zipfile.ZIP_STORED, allowZip64=True) as ausgabe:
            gesamt = len(hauptdateien)
            for nr, name in enumerate(hauptdateien, 1):
                meldung(nr - 1, gesamt, name)
                status, memory = dateien[name]
                quelle = quellen[name]
                extra: dict = {}
                if status == STATUS_AMBIGUOUS:
                    extra["kandidaten"] = " | ".join(
                        f"{m.taken:%H:%M:%S}" + ("" if m.lat is None else f" {m.lat},{m.lon}")
                        for m in ergebnis.candidates.get(name, []))
                try:
                    _eine(name, status, memory, quelle, quellen, offene, modus, pruefer, ausgabe, arbeit, extra, zaehler,
                         chat_von_id, chat_tage)
                except Exception as exc:  # eine kaputte Datei darf den Lauf nicht beenden
                    extra["hinweis"] = f"Fehler: {type(exc).__name__}: {exc}"
                    zaehler["Fehler"] += 1
                zeilen.append(_zeile(name, quelle.zip_pfad.name, status, memory, extra))
                zaehler[status] += 1
            if modus == "alles":
                for m in ergebnis.unassigned:
                    zeilen.append(_zeile("", "", "nicht zugeordnet", m, {}))
            meldung(gesamt, gesamt, "Metadaten-Tabelle")
            puffer = io.StringIO()
            schreiber = csv.writer(puffer, delimiter=";")
            schreiber.writerow(KOPF)
            schreiber.writerows(zeilen)
            ausgabe.writestr(METADATA_NAME, puffer.getvalue().encode("utf-8-sig"), compress_type=zipfile.ZIP_DEFLATED)
    finally:
        for z in offene.values():
            z.close()
        shutil.rmtree(arbeit, ignore_errors=True)
    return zaehler


def _eine(name, status, memory, quelle, quellen, offene, modus, pruefer, ausgabe, arbeit, extra, zaehler,
          chat_von_id, chat_tage):
    z = offene[quelle.zip_pfad]
    suffix = PurePosixPath(name).suffix.lower()
    overlay_name = re.sub(r"-main\.\w+$", "-overlay.png", name)
    overlay = quellen.get(overlay_name) if name.startswith("memories/") else None
    extra["overlay"] = _ja(overlay is not None)
    beschreibung = _chat_hinweis(name, extra, chat_von_id, chat_tage)

    if modus == "alles":
        if suffix in VIDEO_SUFFIXES and overlay is not None:
            daten = _video_verschmolzen(z, quelle, overlay, offene, arbeit)
            extra["verschmolzen"] = "ja"
        else:
            daten = z.read(quelle.mitglied)
            if overlay is not None and suffix in JPEG_SUFFIXES | {".png", ".webp"}:
                daten = merge_image(daten, offene[overlay.zip_pfad].read(overlay.mitglied))
                extra["verschmolzen"] = "ja"
        daten, hinweis = _metadaten(name, daten, status, memory, beschreibung)
        extra["hinweis"] = hinweis
        ausgabe.writestr(_ausgabename(name), daten)
        zaehler["ausgegeben"] += 1
        return

    # gefilterte Modi: nur Bilder
    if overlay is not None:
        zaehler["ausgeschlossen: Overlay-Datei"] += 1
        return
    daten = z.read(quelle.mitglied)
    einbrand, grund = pruefer.einbrand(daten, name)
    extra["einbrand"] = f"{'ja' if einbrand else 'nein'}: {grund}" if grund else _ja(einbrand)
    if einbrand:
        zaehler["ausgeschlossen: eingebrannt (Sticker/Schrift/Linse)"] += 1
        return
    if modus == "personen":
        gesichter, personen = pruefer.gesichter(daten), pruefer.personen(daten)
        extra["gesichter"], extra["personen"] = gesichter, personen
        if not (gesichter or personen):
            zaehler["ausgeschlossen: keine Person"] += 1
            return
    daten, hinweis = _metadaten(name, daten, status, memory, beschreibung)
    extra["hinweis"] = hinweis
    ausgabe.writestr(_ausgabename(name), daten)
    zaehler["ausgegeben"] += 1


_CHAT_NAME = re.compile(r"^chat_media/(\d{4}-\d{2}-\d{2})_(.+)\.\w+$")


def _chat_hinweis(name: str, extra: dict, chat_von_id: dict, chat_tage: dict) -> str | None:
    """Bei Chat-Medien: den Chat nennen, aber nur als Tatsache, wenn der Export ihn über die Media-ID verrät
    (in ZIP 2 bei 4 von 352). Sonst nur die Chats, die an dem Tag aktiv waren, als Hinweis in der Tabelle."""
    found = _CHAT_NAME.match(name)
    if not found:
        return None
    chat = chat_von_id.get(found.group(2))
    if chat:
        extra["chat"] = chat
        return f"Snapchat-Chat: {chat}"
    moegliche = chat_tage.get(found.group(1), [])
    if moegliche:
        extra["moegliche_chats"] = " | ".join(moegliche[:8]) + (" | …" if len(moegliche) > 8 else "")
    return None


def _video_verschmolzen(z, quelle, overlay, offene, arbeit) -> bytes:
    haupt = arbeit / "main.mp4"
    ueber = arbeit / "overlay.png"
    ziel = arbeit / "out.mp4"
    for pfad, (zf, mitglied) in ((haupt, (z, quelle.mitglied)),
                                 (ueber, (offene[overlay.zip_pfad], overlay.mitglied))):
        with zf.open(mitglied) as src, open(pfad, "wb") as dst:
            shutil.copyfileobj(src, dst)
    try:
        merge_video(haupt, ueber, ziel)
        return ziel.read_bytes()
    finally:
        for p in (haupt, ueber, ziel):
            p.unlink(missing_ok=True)
