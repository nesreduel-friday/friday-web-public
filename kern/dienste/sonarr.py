"""Sonarr auf hphome: Serien lesen, Download-Hashes ermitteln, endgültig löschen.

Adresse und API-Schlüssel stehen im Konnektor `sonarr` (Freigaben-Seite). Nur über den
Medien-Unter-Tab erreichbar — kein Werkzeug fürs Modell.
"""

from __future__ import annotations

import asyncio

import httpx

from kern import konnektoren

KONNEKTOR = "sonarr"

# Schützt das Lesen-Ändern-Schreiben der ganzen Serie in staffel_loeschen: löscht
# man zwei Staffeln derselben Serie gleichzeitig, dürfen sich die beiden
# Lesen-Ändern-Schreiben-Zyklen nicht überschneiden (verlorenes Update, eine
# Staffel würde sonst wieder als überwacht gelten). Ein einziges globales Lock
# statt einer Registry je Serie — das ist eine seltene, von Hand ausgelöste
# Aktion, keine Hot-Path-Funktion.
_SERIE_SCHREIB_SPERRE = asyncio.Lock()


class SonarrFehler(Exception):
    """Sonarr ist nicht erreichbar oder lehnt ab — der Text ist für Eike gedacht."""


def _erlaubt() -> None:
    # Default-Deny: „aus“ auf der Freigaben-Seite muss auch hier greifen, nicht
    # nur, wenn Adresse/Schlüssel fehlen (`konnektoren.zugang` allein prüft das
    # nicht). In jeder öffentlichen Funktion aufrufen, NICHT nur in `_ziel()` —
    # `_anfrage` wird in Tests komplett ersetzt, `_ziel()` liefe dann nie.
    if not konnektoren.benutzbar(KONNEKTOR):
        raise SonarrFehler("Sonarr ist ausgeschaltet oder es fehlen Adresse/API-Schlüssel — Freigaben → Sonarr.")


def _ziel() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise SonarrFehler("Für Sonarr fehlen Adresse und API-Schlüssel — Freigaben → Sonarr.")
    adresse = z[0].strip().rstrip("/")
    return (adresse if "://" in adresse else "http://" + adresse), z[1]


async def _anfrage(methode: str, pfad: str, params: dict | None = None, json_body: dict | None = None,
                    timeout: float = 30) -> httpx.Response:
    adresse, schluessel = _ziel()
    kopf = {"X-Api-Key": schluessel}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            return await c.request(methode, adresse + pfad, headers=kopf, params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise SonarrFehler(f"Sonarr antwortet nicht ({type(exc).__name__}).") from None


def _pruefen(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise SonarrFehler("Sonarr lehnt den Schlüssel ab.")
    if r.status_code >= 400:
        raise SonarrFehler(f"Sonarr meldet einen Fehler ({r.status_code}).")


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError as exc:
        raise SonarrFehler(f"Sonarr antwortet nicht mit JSON ({exc}).") from None


async def serien() -> list[dict]:
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/series")
    _pruefen(r)
    return _json(r)


async def serie_loeschen(serie_id: int) -> None:
    _erlaubt()
    r = await _anfrage("DELETE", f"/api/v3/series/{serie_id}", params={"deleteFiles": "true"})
    _pruefen(r)


async def download_hashes(serie_id: int) -> list[str]:
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/history/series", params={"seriesId": serie_id})
    _pruefen(r)
    gesehen: list[str] = []
    for eintrag in _json(r):
        h = eintrag.get("downloadId")
        if h and eintrag.get("eventType") == "downloadFolderImported":
            h = h.lower()
            if h not in gesehen:
                gesehen.append(h)
    return gesehen


async def serie_details(serie_id: int) -> dict:
    _erlaubt()
    r = await _anfrage("GET", f"/api/v3/series/{serie_id}")
    _pruefen(r)
    return _json(r)


async def episoden(serie_id: int) -> list[dict]:
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/episode", params={"seriesId": serie_id})
    _pruefen(r)
    return _json(r)


async def staffel_loeschen(serie_id: int, staffel_nr: int) -> int:
    """Löscht alle heruntergeladenen Folgen dieser Staffel, überwacht sie danach
    nicht mehr (sonst sucht Sonarr sofort neu). Gibt die Anzahl gelöschter
    Dateien zurück."""
    _erlaubt()
    alle = await episoden(serie_id)
    datei_ids = [e["episodeFileId"] for e in alle
                 if e.get("seasonNumber") == staffel_nr and e.get("hasFile") and e.get("episodeFileId")]
    if datei_ids:
        r = await _anfrage("DELETE", "/api/v3/episodefile/bulk", json_body={"episodeFileIds": datei_ids})
        _pruefen(r)

    async with _SERIE_SCHREIB_SPERRE:
        serie = await serie_details(serie_id)
        for saison in serie.get("seasons", []):
            if saison.get("seasonNumber") == staffel_nr:
                saison["monitored"] = False
        r2 = await _anfrage("PUT", f"/api/v3/series/{serie_id}", json_body=serie)
        _pruefen(r2)
    return len(datei_ids)


async def folge_loeschen(episode_id: int, episode_file_id: int | None) -> None:
    """Löscht die Datei dieser Folge (falls vorhanden), überwacht sie danach
    nicht mehr."""
    _erlaubt()
    if episode_file_id:
        r = await _anfrage("DELETE", f"/api/v3/episodefile/{episode_file_id}")
        _pruefen(r)
    r2 = await _anfrage("PUT", "/api/v3/episode/monitor",
                         json_body={"episodeIds": [episode_id], "monitored": False})
    _pruefen(r2)


async def hash_episoden_zuordnung(serie_id: int) -> dict[str, set[int]]:
    """Für jeden Download-Hash dieser Serie: welche Episode-IDs er importiert hat.
    Ein Staffel-/Serienpaket-Torrent taucht hier mit mehreren Episode-IDs auf."""
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/history/series", params={"seriesId": serie_id})
    _pruefen(r)
    zuordnung: dict[str, set[int]] = {}
    for eintrag in _json(r):
        h = eintrag.get("downloadId")
        ep_id = eintrag.get("episodeId")
        if h and ep_id and eintrag.get("eventType") == "downloadFolderImported":
            zuordnung.setdefault(h.lower(), set()).add(ep_id)
    return zuordnung


async def hashes_ausschliesslich_fuer(serie_id: int, episode_ids: set[int]) -> list[str]:
    """Nur die Download-Hashes, die AUSSCHLIESSLICH die übergebenen Folgen
    betreffen. Ein Staffelpaket-Torrent, der auch andere (nicht gelöschte)
    Folgen enthält, wird nicht zurückgegeben — sonst würde qBittorrent beim
    Löschen einer einzelnen Folge den ganzen Torrent samt Dateien der
    anderen Folgen entfernen."""
    zuordnung = await hash_episoden_zuordnung(serie_id)
    return [h for h, betroffene in zuordnung.items() if betroffene and betroffene <= episode_ids]


async def download_hashes_fuer_episoden(episode_ids: list[int]) -> list[str]:
    # /api/v3/history?episodeId=X liefert {"records": [...]}, anders als
    # /api/v3/history/series (reine Liste) — beide Formen abfangen.
    _erlaubt()
    gesehen: list[str] = []
    for episode_id in episode_ids:
        r = await _anfrage("GET", "/api/v3/history", params={"episodeId": episode_id})
        _pruefen(r)
        daten = _json(r)
        eintraege = daten.get("records", []) if isinstance(daten, dict) else daten
        for eintrag in eintraege:
            h = eintrag.get("downloadId")
            if h and eintrag.get("eventType") == "downloadFolderImported":
                h = h.lower()
                if h not in gesehen:
                    gesehen.append(h)
    return gesehen


async def online_suchen(begriff: str) -> list[dict]:
    """Serien aus dem Verzeichnis von Sonarr (TheTVDB), noch nicht unbedingt in der Bibliothek."""
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/series/lookup", params={"term": begriff})
    _pruefen(r)
    return _json(r)


# Marvel-Serien (Marvel Studios und Marvel Television): Sonarr kennt kein Studio, darum Titel + Erscheinungsjahr (±1).
MARVEL_SERIEN = {
    "loki": 2021, "visionquest": 2026, "wandavision": 2021, "the falcon and the winter soldier": 2021, "hawkeye": 2021,
    "moon knight": 2022, "ms marvel": 2022, "she hulk attorney at law": 2022, "secret invasion": 2023, "echo": 2024,
    "agatha all along": 2024, "daredevil born again": 2025, "ironheart": 2025, "what if": 2021, "i am groot": 2022,
    "marvel zombies": 2025, "your friendly neighborhood spider man": 2025, "x men 97": 2024, "agents of s h i e l d": 2013,
    "agent carter": 2015, "jessica jones": 2015, "luke cage": 2016, "iron fist": 2017, "the defenders": 2017,
    "the punisher": 2017, "runaways": 2017, "cloak dagger": 2018, "inhumans": 2017, "helstrom": 2020, "legion": 2017, "the gifted": 2017, "hit monkey": 2021, "m o d o k": 2021,
}


def ist_marvel(titel: str, jahr) -> bool:
    import re
    name = " ".join(re.findall(r"[a-z0-9]+", (titel or "").lower().replace("&", " ").replace("'", "")))
    if name.startswith("marvels ") or name.startswith("marvel "):
        return True
    for k, y in MARVEL_SERIEN.items():
        if name == k or name.startswith(k + " "):
            try:
                return abs(int(jahr) - y) <= 1
            except (TypeError, ValueError):
                return False
    return False


async def hinzufuegen(treffer: dict) -> dict:
    """Legt die Serie an (Qualität und Ordner wie bei den vorhandenen) und lässt Sonarr sofort nach allen Folgen suchen."""
    _erlaubt()
    vorhanden = await serien()
    profile = [s.get("qualityProfileId") for s in vorhanden if s.get("qualityProfileId") not in (None, 1)]
    profil = max(set(profile), key=profile.count) if profile else 6
    r = await _anfrage("GET", "/api/v3/rootfolder")
    _pruefen(r)
    wurzeln = _json(r)
    if not wurzeln:
        raise SonarrFehler("In Sonarr ist kein Stammordner eingerichtet.")
    marvel = next((w["path"] for w in wurzeln if w["path"].rstrip("/").lower().endswith("marvel-serien")), None)
    normal = next((w["path"] for w in wurzeln if w["path"] != marvel), wurzeln[0]["path"])
    ordner = marvel if marvel and ist_marvel(treffer.get("title"), treffer.get("year")) else normal   # Jellyfin-Bibliothek „Marvel Serien“
    body = {**treffer, "qualityProfileId": profil, "rootFolderPath": ordner, "monitored": True,
            "seasonFolder": True,
            "addOptions": {"searchForMissingEpisodes": True, "monitor": "all"}}
    r2 = await _anfrage("POST", "/api/v3/series", json_body=body)
    if r2.status_code == 400:
        raise SonarrFehler("Sonarr lehnt das Hinzufügen ab (vermutlich schon vorhanden).")
    _pruefen(r2)
    return _json(r2)


async def deutsch_releases(serie_id: int, staffel_nr: int) -> list[dict]:
    """Interaktive Suche für eine Staffel: nur Releases, die Sonarr nicht ablehnt und die Deutsch tragen
    (Custom-Format-Punkte > 0 = „Multi Deutsch+Englisch“, oder Sprache German). Beste zuerst."""
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/release", params={"seriesId": serie_id, "seasonNumber": staffel_nr}, timeout=120)
    _pruefen(r)
    treffer = [x for x in _json(r)
               if not x.get("rejected") and (int(x.get("customFormatScore") or 0) > 0
                                             or any((s.get("name") or "") == "German" for s in x.get("languages") or []))]
    return sorted(treffer, key=lambda x: (int(x.get("customFormatScore") or 0), int(x.get("seeders") or 0)), reverse=True)


async def release_holen(release: dict) -> None:
    """Schickt genau dieses Release an den Downloader."""
    _erlaubt()
    r = await _anfrage("POST", "/api/v3/release", json_body={"guid": release["guid"], "indexerId": release["indexerId"]})
    _pruefen(r)
