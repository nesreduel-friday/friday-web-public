"""Die Karten der Übersicht — aus einer Liste von Anbietern zusammengesetzt.

Ein Anbieter ist eine Funktion `async def karten() -> list[dict]` plus ein
Eintrag in `ANBIETER`. Diese Datei kennt keinen Anbieter beim Namen: Sie ruft
die Liste ab, prüft den Konnektor (Default-Deny), fängt Fehler je Anbieter
und sortiert. So braucht ein neuer Anbieter (Wetter, Abfahrt, Termin) keinen
Umbau. Ein Anbieter, der scheitert oder hängt, lässt nur SEINE Karten aus:
die Seite bleibt nie leer oder rot, weil Spotify einen schlechten Tag hat.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from kern import config, konnektoren

log = logging.getLogger("friday.dashboard")

ZEITLIMIT_S = 8   # ein Anbieter, der länger braucht, fällt für diese Abfrage aus


@dataclass(frozen=True)
class Anbieter:
    name: str
    karten: Callable[[], Awaitable[list[dict]]]
    konnektor: str | None   # None = braucht keinen Konnektor
    prio: int               # kleiner = weiter oben


# Gefüllt am Ende dieser Datei (nach den Imports der Anbieter), damit ein
# Test die Liste ersetzen kann.
ANBIETER: list[Anbieter] = []

# Funktionen `(karten_id) -> bool`, die eine Karte als gesehen/weg markieren.
# Jede Ablage meldet sich für ihr Präfix zuständig oder gibt False zurück.
_GESEHEN: list[Callable[[str], bool]] = []


async def _abfragen(a: Anbieter) -> list[dict]:
    if a.konnektor and not konnektoren.benutzbar(a.konnektor):
        return []
    try:
        liste = await asyncio.wait_for(a.karten(), ZEITLIMIT_S)
    except Exception as exc:                       # noqa: BLE001 — eine Karte darf nie die Seite kosten
        log.warning("Dashboard-Anbieter %s fällt aus: %s: %s", a.name, type(exc).__name__, exc)
        return []
    ergebnis = []
    for k in liste or []:
        if not isinstance(k, dict) or not k.get("id") or not k.get("art"):
            log.warning("Dashboard-Anbieter %s lieferte eine Karte ohne id/art", a.name)
            continue
        ergebnis.append({**k, "prio": k.get("prio", a.prio)})
    return ergebnis


async def sammeln() -> dict:
    gruppen = await asyncio.gather(*(_abfragen(a) for a in ANBIETER))
    alle = [k for g in gruppen for k in g]
    alle.sort(key=lambda k: k["prio"])   # stabil: gleiche prio behält die Anbieter-Reihenfolge
    return {"karten": alle, "jetzt": config.jetzt().isoformat()}


def gesehen(karten_id: str) -> bool:
    return any(f(karten_id) for f in _GESEHEN)


from kern.dashboard import friday_karten, meldungen  # noqa: E402  (nach den Definitionen: Anbieter registrieren sich hier)
from kern.dashboard import jellyfin as jellyfinkarte  # noqa: E402
from kern.dashboard import minecraft as minecraftkarte  # noqa: E402
from kern.dashboard import musik as musikkarte  # noqa: E402
from kern.dashboard import pc as pckarte  # noqa: E402
from kern.dashboard import pcmedien as pcmedienkarte  # noqa: E402
from kern.dashboard import regeln as regelkarte  # noqa: E402
from kern.dashboard import routelive  # noqa: E402
from kern.dashboard import schule as schulkarte  # noqa: E402

ANBIETER.extend([
    Anbieter("meldungen", meldungen.karten, None, 10),
    Anbieter("friday", friday_karten.karten, None, 20),
    Anbieter("regeln", regelkarte.karten, None, 25),
    Anbieter("schule", schulkarte.karten, "schulmanager", 30),
    Anbieter("pc", pckarte.karten, "pc", 35),
    Anbieter("musik", musikkarte.karten, "spotify", 40),
    Anbieter("jellyfin", jellyfinkarte.karten, "jellyfin", 42),
    Anbieter("pcmedien", pcmedienkarte.karten, "pc", 44),
    Anbieter("minecraft", minecraftkarte.karten, None, 46),
    Anbieter("route_live", routelive.karten, None, 90),      # liefert keine Karten: hält die Verbindungskarten aktuell
])
_GESEHEN.extend([meldungen.gesehen, friday_karten.gesehen, regelkarte.gesehen])

