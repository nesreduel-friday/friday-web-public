"""Gibt es seit der letzten Denkrunde etwas Neues? Billige Prüfung ohne Modell, vor jeder Runde.

Jede Quelle einzeln in try/except; ein Lesefehler zählt als „neu“ (lieber eine Runde zu viel). Mails und Kalender haben
keine billige Änderungsmarke — sie deckt `MAX_PAUSE_H` ab: Nach so langer Pause läuft eine Runde trotzdem.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from kern import config
from kern.denken.buch import Buch

log = logging.getLogger("friday.denken")

MAX_PAUSE_H = 6


def _zeit(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=config.jetzt().tzinfo)


def _gespraeche(b: Buch, seit: datetime) -> bool:
    from kern.protokoll import speicher as pspeicher
    for g in pspeicher.standard().gespraeche(limit=10):
        if g.get("person") is None and _zeit(g["zuletzt"] or g["beginn"]) > seit:
            return True
    return False


def _notizen(b: Buch, seit: datetime) -> bool:
    from kern.gedaechtnis import speicher as gspeicher
    return any(_zeit(f["gueltig_ab"]) > seit for f in gspeicher.standard().fakten(limit=5))


def _erinnerungen(b: Buch, seit: datetime) -> bool:
    """Ankunftserinnerungen haben keinen Zeitpunkt — sie werden hier nicht „fällig“, sondern bei der Rückkehr."""
    from kern import erinnerungen
    jetzt = config.jetzt()
    return any(e.get("ausloeser") != "ankunft" and seit < _zeit(e["faellig"]) <= jetzt
               for e in erinnerungen.standard().offene(20))


def _faeden(b: Buch, seit: datetime) -> bool:
    return b.faden_fokus() is not None


_gesehen: str | None = None        # zuletzt gesehener Auffälligkeiten-Text (nur im RAM; nach Neustart einmal „neu“)


def _auffaelligkeiten(b: Buch, seit: datetime) -> bool:
    global _gesehen
    from kern.denken import auffaelligkeiten
    jetzt = auffaelligkeiten.text()
    neu = jetzt != (_gesehen if _gesehen is not None else "")
    _gesehen = jetzt
    return neu


QUELLEN = (("gespraeche", _gespraeche), ("notizen", _notizen), ("erinnerungen", _erinnerungen), ("auffaelligkeiten", _auffaelligkeiten),
           ("faeden", _faeden))


def seit(b: Buch, seit: datetime | None) -> list[str]:
    """Namen der Quellen mit Änderung seit `seit`; leer = nichts Neues."""
    if seit is None:
        return ["erste_runde"]
    gefunden = []
    if config.jetzt() - seit >= timedelta(hours=MAX_PAUSE_H):
        gefunden.append("pause")
    for name, fn in QUELLEN:
        try:
            if fn(b, seit):
                gefunden.append(name)
        except Exception as exc:                       # noqa: BLE001
            log.warning("Denkrunde: Neues-Prüfung %s nicht lesbar (%s)", name, type(exc).__name__)
            gefunden.append(name)
    return gefunden
