"""Musikkarte: was gerade auf Spotify läuft, mit Cover und Steuerung.

Spotify wird höchstens alle `CACHE_S` Sekunden gefragt, egal wie oft die
Übersicht abfragt. Ein pausierter Titel bleibt `PAUSE_SICHTBAR` stehen — aber
nur, wenn wir ihn vorher laufen SAHEN: Ob er schon seit Stunden pausiert,
weiß die Spotify-Antwort nicht.
"""

import re
from datetime import datetime, timedelta

from kern import config, konnektoren
from kern.dienste import spotify

CACHE_S = 10
PAUSE_SICHTBAR = timedelta(minutes=10)
BEFEHLE = {"pause": "pause", "weiter": "weiter", "naechster": "naechster", "vorheriger": "vorheriger"}
# Nur diese Form geht als Link in die Seite: spotify:<art>:<id>. Alles andere wird leer.
_SPOTIFY_ADRESSE = re.compile(r"^spotify:(track|episode|album|show|artist):[A-Za-z0-9]{8,40}$")

_stand: dict = {"zeit": None, "daten": None}
_zuletzt_gehoert: datetime | None = None


def _reset() -> None:
    global _zuletzt_gehoert
    _stand.update(zeit=None, daten=None)
    _zuletzt_gehoert = None


async def _laden(jetzt: datetime) -> dict | None:
    if _stand["zeit"] and (jetzt - _stand["zeit"]).total_seconds() < CACHE_S:
        return _stand["daten"]
    daten = await spotify.laeuft_gerade()
    _stand.update(zeit=jetzt, daten=daten)
    return daten


def _adresse(u) -> str:
    return u if isinstance(u, str) and _SPOTIFY_ADRESSE.match(u) else ""


async def karten() -> list[dict]:
    global _zuletzt_gehoert
    jetzt = config.jetzt()
    s = await _laden(jetzt)
    if not s or not s.get("titel"):
        return []
    if s.get("laeuft"):
        _zuletzt_gehoert = jetzt
    elif not _zuletzt_gehoert or jetzt - _zuletzt_gehoert > PAUSE_SICHTBAR:
        return []
    cover = s.get("cover", "")
    return [{"id": "musik", "art": "musik", "titel": s["titel"], "wer": s.get("wer", ""),
             "album": s.get("album", ""), "cover": cover if cover.startswith("https://") else "",
             "laeuft": bool(s.get("laeuft")), "position_ms": s.get("position_ms", 0),
             "dauer_ms": s.get("dauer_ms", 0), "geraet": s.get("geraet", ""),
             "uri": _adresse(s.get("uri")), "album_uri": _adresse(s.get("album_uri")),
             "artist_uri": _adresse(s.get("artist_uri")),
             "steuerbar": konnektoren.schreiben_erlaubt("spotify"), "stand": jetzt.isoformat()}]


async def befehl(name: str) -> None:
    """Pause/Weiter/Nächster aus der Übersicht — dieselben Regeln wie die Werkzeuge:
    nur mit Schreibrecht für Spotify."""
    if name not in BEFEHLE:
        raise ValueError(name)
    if not konnektoren.schreiben_erlaubt("spotify"):
        raise PermissionError("Spotify-Steuerung ist auf der Freigaben-Seite nicht erlaubt.")
    await getattr(spotify, BEFEHLE[name])()
    _stand.update(zeit=None, daten=None)   # nächste Abfrage frisch, nicht aus dem Zwischenspeicher
