"""Medien-Karte vom PC: YouTube im Browser, VLC & Co. — was Windows als aktuelle Mediensitzung kennt.

Wie die PC-Karte fragt `karten()` nie selbst den PC, sondern antwortet aus dem Zwischenspeicher und stößt die
Abfrage im Hintergrund an (ein ausgeschalteter PC darf die Übersicht nicht bremsen). Das Vorschaubild wird pro
Titel einmal geholt und hier gehalten; der Browser bekommt es nur von `/api/pcmedien/bild/<kennung>`.

Spotify und Jellyfin haben eigene Karten — läuft eines davon am PC, bleibt diese hier weg.
"""

import asyncio
import base64
import binascii
import logging
from collections import OrderedDict
from datetime import timedelta

from kern import config, konnektoren
from kern.dashboard import jellyfin as jellyfinkarte
from kern.dienste import pc as pcdienst

log = logging.getLogger("friday.dashboard.pcmedien")

CACHE_S = 5
AUS_CACHE_S = 30
ZEITLIMIT_S = 4
PAUSE_SICHTBAR = timedelta(minutes=10)
BILD_MAX = 600_000
BILDER_MAX = 8
BEFEHLE = ("pause", "weiter", "naechster", "vorheriger")

_stand: dict = {"zeit": None, "daten": None}
_aufgabe: asyncio.Task | None = None
_zuletzt_gehoert = None
_bilder: "OrderedDict[str, tuple[bytes, str] | None]" = OrderedDict()   # Kennung → (Bytes, Typ) oder None (kein Bild)


def _reset() -> None:
    global _aufgabe, _zuletzt_gehoert
    _stand.update(zeit=None, daten=None)
    _aufgabe = _zuletzt_gehoert = None
    _bilder.clear()


def _typ(daten: bytes) -> str:
    return "image/jpeg" if daten[:2] == b"\xff\xd8" else "image/png"


def bild(kennung: str) -> tuple[bytes, str] | None:
    return _bilder.get(kennung)


def _bild_merken(kennung: str, b64: str) -> None:
    try:
        daten = base64.b64decode(b64, validate=True) if b64 else b""
    except (binascii.Error, ValueError):
        daten = b""
    _bilder[kennung] = (daten, _typ(daten)) if 0 < len(daten) <= BILD_MAX else None
    while len(_bilder) > BILDER_MAX:
        _bilder.popitem(last=False)


async def _auffrischen() -> None:
    try:
        m = await asyncio.wait_for(pcdienst.medien(), ZEITLIMIT_S)
        if m and m.get("bild_id") and m["bild_id"] not in _bilder:      # neuer Titel: das Bild einmal dazuholen
            mit = await asyncio.wait_for(pcdienst.medien(mit_bild=True), ZEITLIMIT_S * 3)
            _bild_merken(m["bild_id"], (mit or {}).get("bild", ""))
    except (pcdienst.PcFehler, asyncio.TimeoutError, OSError) as exc:
        log.debug("PC-Medien nicht erreichbar: %s", exc)
        m = None
    except Exception as exc:                        # noqa: BLE001 — die Karte darf nie die Seite kosten
        log.warning("PC-Medien: %s: %s", type(exc).__name__, exc)
        m = None
    _stand.update(zeit=config.jetzt(), daten=m)


def _anstossen() -> None:
    global _aufgabe
    zeit = _stand["zeit"]
    grenze = CACHE_S if _stand["daten"] else AUS_CACHE_S
    if zeit and (config.jetzt() - zeit).total_seconds() < grenze:
        return
    if _aufgabe and not _aufgabe.done():
        return
    _aufgabe = asyncio.get_running_loop().create_task(_auffrischen())


def _norm(t: str) -> str:
    return " ".join(str(t or "").casefold().split())


def _gleich(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return len(a) >= 4 and len(b) >= 4 and (a == b or a in b or b in a)


def _hat_eigene_karte(m: dict) -> bool:
    if "spotify" in f"{m.get('app_kennung', '')} {m.get('app', '')}".lower():
        return True
    jf = jellyfinkarte._stand.get("daten")
    if jf:
        folge = str(jf.get("untertitel") or "").split("·")[-1]
        return any(_gleich(m.get("titel", ""), t) for t in (jf.get("titel"), jf.get("untertitel"), folge))
    return False


def _karte(m: dict) -> dict:
    kennung = m.get("bild_id", "")
    return {"id": "pcmedien", "art": "pcmedien", "titel": m.get("titel") or m.get("app", "Medien"),
            "kuenstler": m.get("kuenstler", ""), "app": m.get("app", ""), "laeuft": bool(m.get("laeuft")),
            "position_s": m.get("position_s", 0), "dauer_s": m.get("dauer_s", 0),
            "bild": f"/api/pcmedien/bild/{kennung}" if _bilder.get(kennung) else "",
            "steuerbar": konnektoren.schreiben_erlaubt("pc"), "stand": _stand["zeit"].isoformat()}


def aktuell() -> dict | None:
    """Für die PC-Seite: die Sitzung, wie sie zuletzt gemeldet wurde — ohne die Regeln der Übersicht (auch Spotify)."""
    _anstossen()
    return _karte(_stand["daten"]) if _stand["daten"] else None


async def karten() -> list[dict]:
    global _zuletzt_gehoert
    _anstossen()
    m = _stand["daten"]
    if not m or _hat_eigene_karte(m):
        return []
    jetzt = config.jetzt()
    if m.get("laeuft"):
        _zuletzt_gehoert = jetzt
    elif not _zuletzt_gehoert or jetzt - _zuletzt_gehoert > PAUSE_SICHTBAR:
        return []
    return [_karte(m)]


async def befehl(name: str) -> None:
    """Pause/Weiter/Nächster/Vorheriger am PC — nur mit „Am PC handeln erlauben“."""
    if name not in BEFEHLE:
        raise ValueError(name)
    if not konnektoren.schreiben_erlaubt("pc"):
        raise PermissionError("„Am PC handeln erlauben“ ist auf der Freigaben-Seite aus.")
    r = await pcdienst.aktion("medien", {"befehl": name})
    if not r.get("ok"):
        raise pcdienst.PcFehler(str(r.get("text") or "Das ging nicht."))
    _stand["zeit"] = None                           # nächste Abfrage frisch
