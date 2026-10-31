"""Der Takt der Denkrunden — alle 2 Stunden, nur tagsüber, nie während Eike spricht.

Alles Zeitliche kommt aus `gedanken.db` (letzte Runde, Runden heute), nicht aus dem Arbeitsspeicher: Ein Neustart von
Friday löst deshalb keine Doppelrunde aus. Der Lauf prüft alle fünf Minuten, ob eine Runde fällig ist (`faellig`).
Schalter „Nachdenken“: `data/einstellungen.json` (`nachdenken_an`, Standard an), geteilt mit dem Morgenbriefing
samt Sperre. `FRIDAY_HINTERGRUND=aus` schaltet wie bei allen Läufen ab (in `main.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from kern import config
from kern.denken import brucke
from kern.denken import buch as gedankenbuch
from kern.denken import neues
from kern.denken import runde as denkrunde
from kern.planer import briefing

log = logging.getLogger("friday.denken")

FENSTER = (7, 23)                  # Stunden: ab 7:00 bis vor 23:00
TAGESLIMIT = 12
RUHE_NACH_GESPRAECH_MIN = 5
PRUEF_TAKT_S = 300
START_VERSATZ_S = 300

_laeuft = False


def takt_min() -> int:
    try:
        return max(30, int(os.getenv("NACHDENKEN_TAKT_MIN", "120")))
    except ValueError:
        return 120


def ist_an() -> bool:
    return briefing._lesen(briefing._einstellungsdatei()).get("nachdenken_an", True) is not False


def einstellen(an: bool) -> None:
    with briefing._sperre:
        d = briefing._lesen(briefing._einstellungsdatei())
        d["nachdenken_an"] = bool(an)
        briefing._schreiben(briefing._einstellungsdatei(), d)


def faellig(b: gedankenbuch.Buch, jetzt: datetime, an: bool, letzte_aktivitaet: datetime | None) -> str:
    """Leer = jetzt denken, sonst der Grund, es nicht zu tun."""
    if not an:
        return "aus"
    if not FENSTER[0] <= jetzt.hour < FENSTER[1]:
        return "nacht"
    if b.runden_heute() >= TAGESLIMIT:
        return "limit"
    if letzte_aktivitaet and jetzt - letzte_aktivitaet < timedelta(minutes=RUHE_NACH_GESPRAECH_MIN):
        return "gespraech"
    letzte = b.letzte_runde_start()
    if letzte and jetzt - letzte < timedelta(minutes=takt_min() - 1):
        return "takt"
    if not neues.seit(b, b.letzte_gute_runde_start()):
        return "nichts_neu"
    return ""


def _letzte_aktivitaet() -> datetime | None:
    try:
        from kern.protokoll import speicher as pspeicher
        g = pspeicher.standard().gespraeche(limit=1)
        return datetime.fromisoformat(g[0]["zuletzt"] or g[0]["beginn"]) if g else None
    except Exception:                                  # noqa: BLE001
        log.warning("Nachdenken: letzte Aktivität nicht lesbar")
        return None


async def jetzt_nachdenken(b: gedankenbuch.Buch | None = None) -> dict:
    """Eine Runde, höchstens eine zur selben Zeit (auch für den Knopf in der App)."""
    global _laeuft
    if _laeuft:
        return {"ok": False, "grund": "Eine Denkrunde läuft gerade."}
    _laeuft = True
    try:
        # Backend je Runde frisch aus der Einstellung (nur Eikes Route schreibt sie)
        if denkrunde.backend() == "opencode":
            return await denkrunde.runde_opencode(b)
        if denkrunde.backend() == "deepseek":
            return await denkrunde.runde_deepseek(b)
        return await denkrunde.runde(b)
    finally:
        _laeuft = False


async def schritt(b: gedankenbuch.Buch | None = None) -> str:
    b = b or gedankenbuch.standard()
    try:
        b.faeden_ruhen_lassen()
    except Exception:                                  # noqa: BLE001
        log.exception("Nachdenken: Fäden konnten nicht ruhen")
    grund = faellig(b, config.jetzt(), ist_an(), _letzte_aktivitaet())
    if grund == "nichts_neu":
        log.info("Nachdenken: nichts Neues seit der letzten Runde, übersprungen")
    if not grund:
        await jetzt_nachdenken(b)
    try:
        await brucke.einspeisen_offene(b)
        await brucke.nachsehen(b)
    except Exception:                                  # noqa: BLE001
        log.exception("Nachdenken: Gedanken konnten nicht an Graphiti gehen")
    return grund


async def dauerlauf() -> None:
    await asyncio.sleep(START_VERSATZ_S)
    while True:
        try:
            await schritt()
        except Exception:                              # noqa: BLE001
            log.exception("Nachdenken: Durchgang fehlgeschlagen")
        await asyncio.sleep(PRUEF_TAKT_S)
