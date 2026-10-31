"""Abschnitt „Auffälligkeiten“ im Überblick der Denkrunde: Fehler an Friday selbst, als fertige Sätze.

Nur Zahlen, Dienstnamen und Statuswörter — nie Gesprächs-, Mail- oder Personentext (auch keine Namen von Personen und keine
Vorhaben-Titel). Jede Quelle einzeln in try/except; höchstens `MAX_ZEILEN` Zeilen.
"""

from __future__ import annotations

import json
import logging
import time

from kern import config

log = logging.getLogger("friday.denken")

MAX_ZEILEN = 10


def _gemini() -> list[str]:
    from kern import schluessel
    heute = schluessel.zaehler_tage(1).get(config.jetzt().strftime("%Y-%m-%d"), {})
    zeilen = []
    for name, modelle in sorted(heute.items()):
        limit = sum(m.get("limit", 0) for m in modelle.values())
        fehler = sum(m.get("fehler", 0) for m in modelle.values())
        if limit or fehler:
            zeilen.append(f"Gemini heute, Schlüssel {name}: {limit} Limit-Antworten (429), {fehler} Serverfehler.")
    return zeilen


def _vorhaben() -> list[str]:
    from kern.werkbank import speicher
    fehler = [v for v in speicher.standard().offene() if v["status"] == "fehler"]
    return [f"Werkbank: {len(fehler)} Vorhaben im Zustand „fehler“ (Worker gescheitert)."] if fehler else []


def _snaps() -> list[str]:
    from kern.dienste import snapchat
    grenze = time.time() - 24 * 3600
    aufgegeben = 0
    datei = snapchat._ordner() / "ergebnisse.jsonl"
    if not datei.exists():
        return []
    for zeile in datei.read_text(encoding="utf-8").splitlines()[-600:]:
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if e.get("ergebnis") == "aufgegeben" and e.get("zeit", 0) >= grenze:
            aufgegeben += 1
    return [f"Snapchat: {aufgegeben} Sendeaufträge in 24 Stunden aufgegeben."] if aufgegeben else []


def text() -> str:
    zeilen: list[str] = []
    for name, fn in (("gemini", _gemini), ("vorhaben", _vorhaben), ("snaps", _snaps)):
        try:
            zeilen += fn()
        except Exception as exc:                       # noqa: BLE001
            log.warning("Denkrunde: Auffälligkeit %s nicht lesbar (%s)", name, type(exc).__name__)
    if not zeilen:
        return ""
    return "Auffälligkeiten an Friday selbst (Zahlen, keine Inhalte):\n" + "\n".join(f"- {z}" for z in zeilen[:MAX_ZEILEN])
