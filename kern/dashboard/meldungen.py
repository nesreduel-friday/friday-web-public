"""Meldungen auf der Übersicht: was `benachrichtigungen.senden` verschickt hat.

Eike bekommt jede dieser Meldungen schon als Push; die Übersicht zeigt sie beim
nächsten Öffnen NOCH EINMAL, damit nichts im Unterricht untergeht, und räumt
sie danach weg: gesehen oder älter als 24 Stunden. Der Haken sitzt in `senden`,
dem einzigen Weg aufs Handy — so gibt es keine zweite Pflege und keine Lücke.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from kern import config

log = logging.getLogger("friday.dashboard")

MAX_ALTER = timedelta(hours=24)
MAX_ANZAHL = 20
TEXT_MAX = 240
_sperre = threading.Lock()


def _pfad() -> Path:
    return config.daten_ordner() / "dashboard_meldungen.json"


def _lesen() -> list[dict]:
    try:
        daten = json.loads(_pfad().read_text(encoding="utf-8"))
        return [m for m in daten if isinstance(m, dict) and m.get("id")]
    except (OSError, ValueError):
        return []


def _schreiben(liste: list[dict]) -> None:
    pfad = _pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(pfad)


def _frisch(m: dict, jetzt: datetime) -> bool:
    if m.get("gesehen"):
        return False
    try:
        return jetzt - datetime.fromisoformat(m["zeit"]) < MAX_ALTER
    except (KeyError, ValueError):
        return False


def ablegen(kennung: str, titel: str, text: str, url: str = "/", tag: str = "") -> None:
    jetzt = config.jetzt()
    neu = {"id": "m" + uuid.uuid4().hex[:8], "quelle": kennung, "titel": (titel or "").strip(),
           "text": (text or "").strip()[:TEXT_MAX], "url": url or "/", "tag": tag or "",
           "zeit": jetzt.isoformat(), "gesehen": False}
    with _sperre:
        liste = [m for m in _lesen() if _frisch(m, jetzt)]
        if tag:   # dieselbe Sache (z. B. dieselbe Fahrt) ersetzt ihre ältere Meldung
            liste = [m for m in liste if not (m.get("tag") == tag and m.get("quelle") == kennung)]
        liste.append(neu)
        _schreiben(liste[-MAX_ANZAHL:])


async def karten() -> list[dict]:
    jetzt = config.jetzt()
    offen = [m for m in _lesen() if _frisch(m, jetzt)]
    offen.sort(key=lambda m: m["zeit"], reverse=True)
    return [{"id": m["id"], "art": "meldung", "quelle": m.get("quelle", ""), "titel": m.get("titel", ""),
             "text": m.get("text", ""), "url": m.get("url", "/"), "zeit": m["zeit"]} for m in offen]


def gesehen(karten_id: str) -> bool:
    if not karten_id.startswith("m"):
        return False
    with _sperre:
        liste = _lesen()
        for m in liste:
            if m["id"] == karten_id:
                m["gesehen"] = True
                _schreiben(liste)
                return True
    return False
