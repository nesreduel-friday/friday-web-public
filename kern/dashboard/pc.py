"""PC-Karte: ist Eikes PC an, seit wann, was ist offen — und was wartet auf seinen Tipp.

Der PC liegt im Tailnet und kann aus sein; ein Verbindungsversuch dauert dann bis zur Zeitgrenze.
Damit die Übersicht nie darauf wartet, antwortet `karten()` nur aus dem Zwischenspeicher und stößt
die Abfrage im Hintergrund an. Die allererste Abfrage nach dem Start liefert deshalb noch keine Karte.
"""

import asyncio
import logging

from kern import config, konnektoren
from kern.dienste import pc as pcdienst

log = logging.getLogger("friday.dashboard.pc")

CACHE_S = 15          # so alt darf ein Stand sein, wenn der PC an ist
AUS_CACHE_S = 30      # … wenn er nicht antwortet (nicht öfter anklopfen)
ZEITLIMIT_S = 3

_stand: dict = {"zeit": None, "daten": None}
_aufgabe: asyncio.Task | None = None


def _reset() -> None:
    global _aufgabe
    _stand.update(zeit=None, daten=None)
    _aufgabe = None


async def _auffrischen() -> None:
    try:
        daten = await asyncio.wait_for(pcdienst.status(), ZEITLIMIT_S)
        daten = daten if daten.get("ok") else None
    except (pcdienst.PcFehler, asyncio.TimeoutError, OSError) as exc:
        log.debug("PC nicht erreichbar: %s", exc)
        daten = None
    except Exception as exc:                        # noqa: BLE001 — die Karte darf nie die Seite kosten
        log.warning("PC-Karte: %s: %s", type(exc).__name__, exc)
        daten = None
    _stand.update(zeit=config.jetzt(), daten=daten)


def _anstossen() -> None:
    """Startet eine Abfrage im Hintergrund, wenn der Stand veraltet ist und keine läuft."""
    global _aufgabe
    zeit = _stand["zeit"]
    grenze = CACHE_S if _stand["daten"] else AUS_CACHE_S
    if zeit and (config.jetzt() - zeit).total_seconds() < grenze:
        return
    if _aufgabe and not _aufgabe.done():
        return
    _aufgabe = asyncio.get_running_loop().create_task(_auffrischen())


async def karten() -> list[dict]:
    _anstossen()
    s = _stand["daten"]
    if not s:
        return []
    return [{"id": "pc", "art": "pc", "rechner": s.get("rechner", "PC"), "laufzeit_s": s.get("laufzeit_s"),
             "gesperrt": bool(s.get("gesperrt")), "fenster": list(s.get("fenster") or []),
             "helfer_neu": pcdienst.helfer_version() if pcdienst.helfer_neuer(s.get("version"), pcdienst.helfer_version()) else "",
             "schreiben": konnektoren.schreiben_erlaubt("pc"),
             "offen": [{"id": o["id"], "beschreibung": o["beschreibung"], "rest_s": o["rest_s"]}
                       for o in pcdienst.offene()],
             "stand": _stand["zeit"].isoformat()}]
