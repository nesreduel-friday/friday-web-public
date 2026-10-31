"""Jellyfin-Karte: was gerade läuft (jedes Gerät), mit Vorschaubild und Steuerung.

Jellyfin wird höchstens alle `CACHE_S` Sekunden gefragt. Pausiert bleibt die Karte `PAUSE_SICHTBAR`
stehen — aber nur, wenn wir es vorher laufen SAHEN.
"""

from datetime import timedelta

from kern import config, konnektoren
from kern.dienste import jellyfin

CACHE_S = 5
PAUSE_SICHTBAR = timedelta(minutes=10)
BEFEHLE = ("pause", "weiter", "naechster", "vorheriger")

_stand: dict = {"zeit": None, "daten": None}
_zuletzt_gesehen = None


def _reset() -> None:
    global _zuletzt_gesehen
    _stand.update(zeit=None, daten=None)
    _zuletzt_gesehen = None


async def _laden(jetzt) -> dict | None:
    if _stand["zeit"] and (jetzt - _stand["zeit"]).total_seconds() < CACHE_S:
        return _stand["daten"]
    daten = await jellyfin.laeuft_gerade()
    _stand.update(zeit=jetzt, daten=daten)
    return daten


async def karten() -> list[dict]:
    global _zuletzt_gesehen
    jetzt = config.jetzt()
    try:
        s = await _laden(jetzt)
    except jellyfin.JellyfinFehler:
        return []
    if not s:
        return []
    if s["laeuft"]:
        _zuletzt_gesehen = jetzt
    elif not _zuletzt_gesehen or jetzt - _zuletzt_gesehen > PAUSE_SICHTBAR:
        return []
    return [{"id": "jellyfin", "art": "jellyfin", "titel": s["titel"], "untertitel": s["untertitel"],
             "geraet": s["geraet"], "laeuft": s["laeuft"], "position_s": s["position_s"], "dauer_s": s["dauer_s"],
             "bild": f"/api/jellyfin/bild/{s['bild_id']}" if s["bild_id"] else "",
             "steuerbar": bool(s["steuerbar"]) and konnektoren.schreiben_erlaubt("jellyfin"),
             "stand": jetzt.isoformat()}]


async def befehl(name: str) -> None:
    """Pause/Weiter/Nächster/Vorheriger aus der Übersicht — nur mit Schreibrecht für Jellyfin."""
    if name not in BEFEHLE:
        raise ValueError(name)
    if not konnektoren.schreiben_erlaubt("jellyfin"):
        raise PermissionError("Jellyfin-Steuerung ist auf der Freigaben-Seite nicht erlaubt.")
    await jellyfin.befehl(name)
    _stand.update(zeit=None, daten=None)   # nächste Abfrage frisch, nicht aus dem Zwischenspeicher
