"""Radarr auf hphome: Filme lesen, Download-Hashes ermitteln, endgültig löschen.

Adresse und API-Schlüssel stehen im Konnektor `radarr` (Freigaben-Seite). Nur über den
Medien-Unter-Tab erreichbar — kein Werkzeug fürs Modell.
"""

from __future__ import annotations

import re

import httpx

from kern import konnektoren

KONNEKTOR = "radarr"


class RadarrFehler(Exception):
    """Radarr ist nicht erreichbar oder lehnt ab — der Text ist für Eike gedacht."""


def _erlaubt() -> None:
    # Default-Deny: „aus“ auf der Freigaben-Seite muss auch hier greifen (siehe
    # sonarr.py — `_anfrage` wird in Tests ersetzt, ein Check nur in `_ziel()`
    # würde dann nie laufen).
    if not konnektoren.benutzbar(KONNEKTOR):
        raise RadarrFehler("Radarr ist ausgeschaltet oder es fehlen Adresse/API-Schlüssel — Freigaben → Radarr.")


def _ziel() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise RadarrFehler("Für Radarr fehlen Adresse und API-Schlüssel — Freigaben → Radarr.")
    adresse = z[0].strip().rstrip("/")
    return (adresse if "://" in adresse else "http://" + adresse), z[1]


async def _anfrage(methode: str, pfad: str, params: dict | None = None, timeout: float = 30,
                    json_body: dict | None = None) -> httpx.Response:
    adresse, schluessel = _ziel()
    kopf = {"X-Api-Key": schluessel}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            return await c.request(methode, adresse + pfad, headers=kopf, params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise RadarrFehler(f"Radarr antwortet nicht ({type(exc).__name__}).") from None


def _pruefen(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise RadarrFehler("Radarr lehnt den Schlüssel ab.")
    if r.status_code >= 400:
        raise RadarrFehler(f"Radarr meldet einen Fehler ({r.status_code}).")


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError as exc:
        raise RadarrFehler(f"Radarr antwortet nicht mit JSON ({exc}).") from None


async def filme() -> list[dict]:
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/movie")
    _pruefen(r)
    return _json(r)


async def film_loeschen(film_id: int) -> None:
    _erlaubt()
    r = await _anfrage("DELETE", f"/api/v3/movie/{film_id}", params={"deleteFiles": "true"})
    _pruefen(r)


async def download_hashes(film_id: int) -> list[str]:
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/history/movie", params={"movieId": film_id})
    _pruefen(r)
    gesehen: list[str] = []
    for eintrag in _json(r):
        h = eintrag.get("downloadId")
        if h and eintrag.get("eventType") == "downloadFolderImported":
            h = h.lower()
            if h not in gesehen:
                gesehen.append(h)
    return gesehen


async def online_suchen(begriff: str) -> list[dict]:
    """Filme aus dem Verzeichnis von Radarr (TMDB), noch nicht unbedingt in der Bibliothek."""
    _erlaubt()
    r = await _anfrage("GET", "/api/v3/movie/lookup", params={"term": begriff})
    _pruefen(r)
    return _json(r)


# Sammlungen (TMDB-Titel) aus dem Marvel-Umfeld: MCU, Fox-X-Men, Sonys Spider-Man, Venom & Co. Wortgrenzen, damit z. B.
# „Blade Runner“ nicht als „Blade“ zählt.
MARVEL_SAMMLUNGEN = re.compile(
    r"\b(x-men|wolverine|deadpool|fantastic four|spider-man|spider-verse|venom|ghost rider|guardians of the galaxy|black panther|"
    r"captain america|captain marvel|thor|ant-man|doctor strange|avengers|iron man|hulk|eternals|shang-chi|punisher|daredevil|"
    r"morbius|kraven|madame web|new mutants|marvel|mcu)\b|^blade collection$")


def ist_marvel(film: dict) -> bool:
    """Marvel Studios als Studio oder eine Marvel-Sammlung (auch Fox/Sony-Filme wie X-Men, Deadpool, Spider-Man)."""
    sammlung = ((film.get("collection") or {}).get("title") or "").lower()
    return "marvel" in (film.get("studio") or "").lower() or bool(MARVEL_SAMMLUNGEN.search(sammlung))


async def hinzufuegen(treffer: dict) -> dict:
    """Legt den Film an (Qualität und Ordner wie bei den vorhandenen) und lässt Radarr sofort danach suchen."""
    _erlaubt()
    vorhanden = await filme()
    profile = [f.get("qualityProfileId") for f in vorhanden if f.get("qualityProfileId") not in (None, 1)]
    profil = max(set(profile), key=profile.count) if profile else 6
    r = await _anfrage("GET", "/api/v3/rootfolder")
    _pruefen(r)
    wurzeln = _json(r)
    if not wurzeln:
        raise RadarrFehler("In Radarr ist kein Stammordner eingerichtet.")
    ordner = wurzeln[0]["path"]
    if ist_marvel(treffer):                                     # Jellyfin-Bibliothek „Marvel“
        ordner = next((w["path"] for w in wurzeln if w["path"].rstrip("/").lower().endswith("/marvel")), ordner)
    body = {**treffer, "qualityProfileId": profil, "rootFolderPath": ordner, "monitored": True,
            "minimumAvailability": "released", "addOptions": {"searchForMovie": True}}
    r2 = await _anfrage("POST", "/api/v3/movie", json_body=body)
    if r2.status_code == 400:
        raise RadarrFehler("Radarr lehnt das Hinzufügen ab (vermutlich schon vorhanden).")
    _pruefen(r2)
    return _json(r2)


async def suche_starten(film_id: int) -> None:
    """Lässt Radarr für diesen Film suchen. Radarr lädt nur, was nach Profil und Custom Formats besser ist als die vorhandene Datei."""
    _erlaubt()
    r = await _anfrage("POST", "/api/v3/command", json_body={"name": "MoviesSearch", "movieIds": [int(film_id)]})
    _pruefen(r)
