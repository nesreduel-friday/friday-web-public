"""qBittorrent auf hphome: passende Torrents zu gelöschten Downloads entfernen.

Nutzername und Passwort stehen im Konnektor `qbittorrent` (Freigaben-Seite). Die Adresse
ist fest — qBittorrent läuft auf demselben Host wie Friday, nicht nutzerkonfigurierbar.
Login zeigt sich am gesetzten Session-Cookie (Name portspezifisch, `QBT_SID_<port>`),
nicht am Antworttext: qBittorrent 5.x antwortet mit Status 204 und leerem Body.
"""

from __future__ import annotations

import httpx

from kern import konnektoren

KONNEKTOR = "qbittorrent"
ADRESSE = "http://host.docker.internal:8080"


class QbittorrentFehler(Exception):
    """qBittorrent ist nicht erreichbar oder lehnt ab — der Text ist für Eike gedacht."""


def _zugang() -> tuple[str, str]:
    # Default-Deny: „aus“ auf der Freigaben-Seite muss auch hier greifen (siehe
    # sonarr.py — nicht nur prüfen, ob Zugangsdaten eingetragen sind).
    if not konnektoren.benutzbar(KONNEKTOR):
        raise QbittorrentFehler("qBittorrent ist ausgeschaltet oder es fehlen Nutzername/Passwort — Freigaben → qBittorrent.")
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise QbittorrentFehler("Für qBittorrent fehlen Nutzername und Passwort — Freigaben → qBittorrent.")
    return z


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=ADRESSE, timeout=30)


async def torrents_entfernen(hashes: list[str], loeschen: bool) -> list[str]:
    if not hashes:
        return []
    nutzer, passwort = _zugang()
    try:
        async with _client() as c:
            anmeldung = await c.post("/api/v2/auth/login", data={"username": nutzer, "password": passwort})
            if anmeldung.status_code not in (200, 204) or len(c.cookies) == 0:
                raise QbittorrentFehler("qBittorrent lehnt Nutzername/Passwort ab.")

            liste = await c.get("/api/v2/torrents/info")
            if liste.status_code != 200:
                raise QbittorrentFehler(f"qBittorrent meldet einen Fehler ({liste.status_code}).")

            vorhanden = {t["hash"] for t in liste.json()}
            treffer = [h for h in hashes if h in vorhanden]
            if not treffer:
                return []

            entfernen = await c.post(
                "/api/v2/torrents/delete",
                data={"hashes": "|".join(treffer), "deleteFiles": "true" if loeschen else "false"},
            )
            if entfernen.status_code != 200:
                raise QbittorrentFehler(f"qBittorrent-Löschung fehlgeschlagen ({entfernen.status_code}).")
            return treffer
    except httpx.HTTPError as exc:
        raise QbittorrentFehler(f"qBittorrent antwortet nicht ({type(exc).__name__}).") from None
