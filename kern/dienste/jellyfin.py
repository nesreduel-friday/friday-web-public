"""Jellyfin auf hphome: was läuft gerade, Vorschaubild, Steuerung.

Adresse und API-Schlüssel stehen im Konnektor `jellyfin` (Freigaben-Seite). Der Schlüssel geht nur im
Kopf der Anfrage an Jellyfin — nie in eine Adresse, die der Browser sieht: Das Bild reicht Friday selbst durch.
"""

from __future__ import annotations

import re

import httpx

from kern import konnektoren

KONNEKTOR = "jellyfin"
TICKS_PRO_S = 10_000_000
BILD_MAX = 2_000_000
_KENNUNG = re.compile(r"^[0-9a-f]{32}$")
# „pause“ und „weiter“ gehen als PlayPause: Der Web-Player quittiert Playing/Pause und /Unpause mit 204 und
# tut nichts (25.09.2026 am echten Chrome gemessen), PlayPause geht. Weil PlayPause umschaltet, wird es nur
# gesendet, wenn die Sitzung gerade das Gegenteil tut.
_BEFEHLE = {"pause": "PlayPause", "weiter": "PlayPause", "naechster": "NextTrack", "vorheriger": "PreviousTrack"}


class JellyfinFehler(Exception):
    """Jellyfin ist nicht erreichbar oder lehnt ab — der Text ist für Eike gedacht."""


def _ziel() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise JellyfinFehler("Für Jellyfin fehlen Adresse und Schlüssel — Freigaben → Jellyfin.")
    adresse = z[0].strip().rstrip("/")
    return (adresse if "://" in adresse else "http://" + adresse), z[1]


async def _anfrage(methode: str, pfad: str, timeout: float = 5) -> httpx.Response:
    adresse, schluessel = _ziel()
    kopf = {"Authorization": f'MediaBrowser Client="Friday", Device="hphome", DeviceId="friday", '
                             f'Version="1", Token="{schluessel}"'}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            return await c.request(methode, adresse + pfad, headers=kopf)
    except httpx.HTTPError as exc:
        raise JellyfinFehler(f"Jellyfin antwortet nicht ({type(exc).__name__}).") from None


def _pruefen(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise JellyfinFehler("Jellyfin lehnt den Schlüssel ab.")
    if r.status_code >= 400:
        raise JellyfinFehler(f"Jellyfin meldet einen Fehler ({r.status_code}).")


def _bild_id(i: dict) -> str:
    if (i.get("ImageTags") or {}).get("Primary"):
        return i.get("Id", "")
    if i.get("AlbumPrimaryImageTag") and i.get("AlbumId"):
        return i["AlbumId"]
    if i.get("SeriesPrimaryImageTag") and i.get("SeriesId"):
        return i["SeriesId"]
    return ""


def _texte(i: dict) -> tuple[str, str]:
    art = i.get("Type")
    if art == "Episode":
        folge = f"S{i.get('ParentIndexNumber', '?')} E{i.get('IndexNumber', '?')} · {i.get('Name', '')}"
        return i.get("SeriesName") or i.get("Name", ""), folge
    if art == "Audio":
        return i.get("Name", ""), ", ".join(i.get("Artists") or []) or i.get("Album", "")
    return i.get("Name", ""), str(i.get("ProductionYear") or "")


def _sitzung(s: dict) -> dict:
    i, p = s["NowPlayingItem"], s.get("PlayState") or {}
    titel, untertitel = _texte(i)
    return {"sitzung": s["Id"], "titel": titel, "untertitel": untertitel, "geraet": s.get("DeviceName", ""),
            "laeuft": not p.get("IsPaused"), "position_s": int((p.get("PositionTicks") or 0) // TICKS_PRO_S),
            "dauer_s": int((i.get("RunTimeTicks") or 0) // TICKS_PRO_S), "bild_id": _bild_id(i),
            "steuerbar": bool(s.get("SupportsRemoteControl"))}


async def laeuft_gerade() -> dict | None:
    """Die zuletzt aktive Sitzung mit laufendem oder pausiertem Inhalt — oder None."""
    r = await _anfrage("GET", "/Sessions")
    _pruefen(r)
    mit = [s for s in r.json() if isinstance(s, dict) and s.get("NowPlayingItem")]
    if not mit:
        return None
    return _sitzung(max(mit, key=lambda s: s.get("LastActivityDate") or ""))


async def befehl(name: str) -> None:
    if name not in _BEFEHLE:
        raise ValueError(name)
    s = await laeuft_gerade()
    if not s:
        raise JellyfinFehler("Gerade läuft nichts auf Jellyfin.")
    if not s["steuerbar"]:
        raise JellyfinFehler("Dieses Gerät lässt sich von Jellyfin aus nicht fernsteuern.")
    if (name == "pause" and not s["laeuft"]) or (name == "weiter" and s["laeuft"]):
        return                                      # ist schon so — ein Umschalten wäre das Gegenteil
    _pruefen(await _anfrage("POST", f"/Sessions/{s['sitzung']}/Playing/{_BEFEHLE[name]}"))


async def bild(item_id: str) -> tuple[bytes, str]:
    if not _KENNUNG.match(item_id):
        raise ValueError(item_id)
    r = await _anfrage("GET", f"/Items/{item_id}/Images/Primary?maxWidth=640&quality=80")
    _pruefen(r)
    typ = r.headers.get("content-type", "").split(";")[0].strip()
    if not typ.startswith("image/") or len(r.content) > BILD_MAX:
        raise JellyfinFehler("Jellyfin lieferte kein brauchbares Bild.")
    return r.content, typ
