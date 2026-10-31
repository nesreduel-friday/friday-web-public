"""Medien-Tab (Extras → hphome → Medien): Duplikate zwischen Downloads und Bibliothek
finden/löschen, eine Serie/Film überall korrekt löschen (Sonarr/Radarr + qBittorrent).

Erreichbar über den Tab und (seit 08.10.2026) über die Werkzeuge in `kern/werkzeuge/medien.py`
(Löschen nur mit Rückfrage und Eikes Ja).
"""

from __future__ import annotations

import filecmp
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kern import config
from kern.dienste import qbittorrent, radarr, sonarr


def medien_wurzel() -> Path:
    return Path(os.environ.get("MEDIA_WURZEL", "/media-data"))


@dataclass
class Duplikat:
    download_pfad: str
    bibliothek_pfad: str
    groesse: int


def _nach_groesse(wurzel: Path) -> dict[int, list[Path]]:
    index: dict[int, list[Path]] = {}
    for ordner, _u, dateien in os.walk(wurzel):
        for name in dateien:
            pfad = Path(ordner) / name
            try:
                groesse = pfad.stat().st_size
            except OSError:
                continue
            index.setdefault(groesse, []).append(pfad)
    return index


def find_duplikate(downloads: Path, bibliothek: Path) -> list[Duplikat]:
    # filecmp merkt sich Vergleiche nach (Pfad, Größe, mtime) über Aufrufe hinweg —
    # bei gleicher Größe UND (durch Dateisystem-Auflösung oder Zufall) gleicher
    # mtime könnte sonst ein veraltetes, falsches Ergebnis herauskommen.
    filecmp.clear_cache()
    nach_groesse_bibliothek = _nach_groesse(bibliothek)
    treffer: list[Duplikat] = []
    for groesse, download_pfade in _nach_groesse(downloads).items():
        kandidaten = nach_groesse_bibliothek.get(groesse, [])
        if not kandidaten:
            continue
        for download_pfad in download_pfade:
            for bibliothek_pfad in kandidaten:
                try:
                    d_stat = download_pfad.stat()
                    b_stat = bibliothek_pfad.stat()
                except OSError:
                    continue  # Datei ist während des Scans verschwunden
                if (d_stat.st_dev, d_stat.st_ino) == (b_stat.st_dev, b_stat.st_ino):
                    continue  # schon ein Hardlink — kein Platz zu gewinnen
                try:
                    identisch = filecmp.cmp(download_pfad, bibliothek_pfad, shallow=False)
                except OSError:
                    continue  # Datei ist zwischen stat() und dem Lesen verschwunden
                if identisch:
                    treffer.append(Duplikat(str(download_pfad), str(bibliothek_pfad), groesse))
                    break
    return treffer


def duplikat_loeschen(pfad: Path) -> None:
    pfad.unlink()


ALREADY_RUNNING = "Löschvorgang für dieses Element läuft bereits"

_IN_PROGRESS: set[tuple[str, int | str]] = set()  # Staffel-Schlüssel ist "serie_id:staffel_nr" (str)
_LOCK = threading.Lock()


@dataclass
class LoeschErgebnis:
    hauptteil_ok: bool
    qbittorrent_entfernt: list[str]
    fehler: str | None


def _protokoll_pfad() -> Path:
    return config.daten_ordner() / "medien" / "aktionen.log"


def _protokollieren(text: str) -> None:
    # Best-effort: eine volle Platte o.ä. darf einen bereits erfolgten
    # Löschvorgang nicht nachträglich in einen Fehler verwandeln.
    try:
        pfad = _protokoll_pfad()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        zeit = datetime.now(timezone.utc).isoformat()
        with pfad.open("a", encoding="utf-8") as f:
            f.write(f"{zeit} {text}\n")
    except OSError:
        pass


async def _ablauf(art: str, id_: int, titel: str, hashes_holen, primaer_loeschen) -> LoeschErgebnis:
    schluessel = (art, id_)
    with _LOCK:
        if schluessel in _IN_PROGRESS:
            return LoeschErgebnis(False, [], ALREADY_RUNNING)
        _IN_PROGRESS.add(schluessel)

    try:
        hashes: list[str] = []
        hash_fehler: Exception | None = None
        try:
            hashes = await hashes_holen()
        except Exception as exc:
            hash_fehler = exc

        try:
            await primaer_loeschen()
        except Exception as exc:
            _protokollieren(f"FEHLER {art} '{titel}' (id={id_}) nicht gelöscht: {exc}")
            return LoeschErgebnis(False, [], str(exc))

        if hash_fehler is not None:
            _protokollieren(
                f"TEILWEISE {art} '{titel}' (id={id_}) gelöscht, "
                f"qBittorrent-Hashes nicht ermittelbar: {hash_fehler}"
            )
            return LoeschErgebnis(True, [], f"qBittorrent-Hashes nicht ermittelbar: {hash_fehler}")

        try:
            entfernt = await qbittorrent.torrents_entfernen(hashes, loeschen=True)
        except Exception as exc:
            _protokollieren(f"TEILWEISE {art} '{titel}' (id={id_}) gelöscht, qBittorrent fehlgeschlagen: {exc}")
            return LoeschErgebnis(True, [], f"qBittorrent: {exc}")

        _protokollieren(f"OK {art} '{titel}' (id={id_}) gelöscht, qBittorrent entfernt: {entfernt}")
        return LoeschErgebnis(True, entfernt, None)
    finally:
        with _LOCK:
            _IN_PROGRESS.discard(schluessel)


async def serie_ueberall_loeschen(serie_id: int, titel: str) -> LoeschErgebnis:
    return await _ablauf(
        "serie", serie_id, titel,
        hashes_holen=lambda: sonarr.download_hashes(serie_id),
        primaer_loeschen=lambda: sonarr.serie_loeschen(serie_id),
    )


async def film_ueberall_loeschen(film_id: int, titel: str) -> LoeschErgebnis:
    return await _ablauf(
        "film", film_id, titel,
        hashes_holen=lambda: radarr.download_hashes(film_id),
        primaer_loeschen=lambda: radarr.film_loeschen(film_id),
    )


async def staffel_ueberall_loeschen(serie_id: int, staffel_nr: int, titel: str) -> LoeschErgebnis:
    async def hashes_holen():
        alle = await sonarr.episoden(serie_id)
        episode_ids = {e["id"] for e in alle if e.get("seasonNumber") == staffel_nr and e.get("hasFile")}
        # Nur Hashes, die AUSSCHLIESSLICH diese Staffel betreffen — ein
        # Staffelpaket-Torrent, der auch andere Staffeln enthält, bleibt
        # unangetastet (siehe sonarr.hashes_ausschliesslich_fuer).
        return await sonarr.hashes_ausschliesslich_fuer(serie_id, episode_ids)

    return await _ablauf(
        "staffel", f"{serie_id}:{staffel_nr}", titel,
        hashes_holen=hashes_holen,
        primaer_loeschen=lambda: sonarr.staffel_loeschen(serie_id, staffel_nr),
    )


async def folge_ueberall_loeschen(serie_id: int, episode_id: int, titel: str) -> LoeschErgebnis:
    async def primaer_loeschen():
        # episodeFileId serverseitig nachschlagen statt vom Aufrufer zu
        # übernehmen — eine veraltete ID aus dem Browser (z.B. nach einem
        # zweiten Klick auf eine schon gelöschte Folge) darf nie die Datei
        # einer ANDEREN Folge treffen.
        alle = await sonarr.episoden(serie_id)
        eintrag = next((e for e in alle if e.get("id") == episode_id), None)
        datei_id = eintrag.get("episodeFileId") if eintrag else None
        await sonarr.folge_loeschen(episode_id, datei_id)

    return await _ablauf(
        "folge", episode_id, titel,
        hashes_holen=lambda: sonarr.hashes_ausschliesslich_fuer(serie_id, {episode_id}),
        primaer_loeschen=primaer_loeschen,
    )
