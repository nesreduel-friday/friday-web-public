"""Der laufende Regel-Lauf (15.10.2026): Welche Regel läuft gerade, welches Ereignis hat sie ausgelöst, was wurde getan?

Die Aktionswerkzeuge (`werkzeuge/regel_aktionen.py`) lesen die Grenzen NUR von hier — nie aus den Argumenten des Modells.
Ohne gesetzten Kontext tut keines etwas. Je Lauf (asyncio-Task) ein eigener Wert (`contextvars`)."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Kontext:
    regel: dict
    ereignis: dict          # kanal, absender_id, absender_name, text, ereignis_id
    aktionen: list = field(default_factory=list)      # je Aktion {"werkzeug", "kurz", "status"} — fürs Protokoll
    lauf_id: int | None = None                        # der Lauf in `speicher.laeufe`: Aktionen werden sofort dorthin geschrieben
    gesendet: list = field(default_factory=list)      # Ziele ("art:id"), an die in diesem Lauf schon gesendet wurde


_aktuell: contextvars.ContextVar = contextvars.ContextVar("regel_kontext", default=None)


@contextmanager
def setzen(k: Kontext):
    token = _aktuell.set(k)
    try:
        yield k
    finally:
        _aktuell.reset(token)


def aktuell() -> Kontext | None:
    return _aktuell.get()
