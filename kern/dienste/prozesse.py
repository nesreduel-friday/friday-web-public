"""Welche Hintergrundläufe gerade laufen oder abgestürzt sind — für die Einstellungen.

Rein im Arbeitsspeicher: Ein Neustart von Friday setzt das bewusst zurück, denn dann
laufen die Hintergrundläufe ja neu an. `kern/main.py` ruft `gestartet()` beim Start
jeder Hintergrund-Aufgabe und `abgestuerzt()` in ihrem `done_callback` — ein Absturz
ist sonst nur im Log sichtbar.
"""

from __future__ import annotations

import time

_stand: dict[str, dict] = {}


def gestartet(name: str) -> None:
    _stand[name] = {"seit": time.time(), "fehler": None}


def abgestuerzt(name: str, grund: str) -> None:
    eintrag = _stand.setdefault(name, {"seit": time.time(), "fehler": None})
    eintrag["fehler"] = grund[:300]


def uebersicht() -> list[dict]:
    return [{"name": name, "laeuft": e["fehler"] is None, "seit": e["seit"], "fehler": e["fehler"]}
            for name, e in sorted(_stand.items())]
