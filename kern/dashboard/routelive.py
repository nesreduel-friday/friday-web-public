"""Live: Die Verbindungskarte hält sich selbst aktuell, solange Eike sie ansieht.

Die Übersicht fragt `/api/dashboard` alle paar Sekunden (nur bei sichtbarer App). Dieser Anbieter liefert selbst keine Karten, stößt aber
bei jeder Abfrage im Hintergrund die Auffrischung fälliger Verbindungskarten an — nie blockierend, nie doppelt:

- **Standort:** neu aus OwnTracks bzw. der App, OHNE das Handy zu fragen (`frisch=False`). Ist er nicht brauchbar, bleibt die Karte stehen und wird
  nach `VERALTET_S` als „nicht aktuell“ gekennzeichnet — es wird nichts erfunden.
- **Zeiten:** Abfahrten, Verspätungen, Ankunft neu von GVH und Routendienst (`routen.alle`); eine gewünschte spätere Zeit bleibt, bis sie da ist.
- **Ende:** Ist Eike am Ziel (< `SCHON_DA_M`), gilt die Karte noch `ANGEKOMMEN_MIN` Minuten als „angekommen“. Solange er sie ansieht,
  wird sie um `VERLAENGERN_MIN` verlängert.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from kern import config
from kern.dashboard import friday_karten
from kern.dienste import routen, standort
from kern.werkzeuge import route as routewerkzeug

log = logging.getLogger("friday.dashboard.routelive")

LIVE_S = 30                 # so alt darf der Stand einer Karte werden, bevor neu gerechnet wird
VERALTET_S = 180            # ohne neue Zahlen so lange, dann „nicht aktuell“
VERLAENGERN_MIN = 15
ANGEKOMMEN_MIN = 3

_aufgabe: asyncio.Task | None = None


def _reset() -> None:
    global _aufgabe
    _aufgabe = None


def _alter_s(k: dict, jetzt: datetime) -> float:
    try:
        return (jetzt - datetime.fromisoformat(k["daten"]["stand"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return 10 ** 9


async def auffrischen(k: dict) -> None:
    jetzt = config.jetzt()
    daten, auftrag = k["daten"], k["daten"]["auftrag"]
    ziel = auftrag["ziel"]
    hinweise: list[str] = []
    try:
        if auftrag.get("start"):
            von = auftrag["start"]
        else:
            von, fehler = await routewerkzeug._eigener_start(hinweise, frisch=False, behalte=daten.get("start"))
            if fehler:                                          # kein brauchbarer Standort: nichts Neues erfinden
                _veraltet(k, jetzt)
                return
            if standort.abstand_m((von["breite"], von["laenge"]), (ziel["breite"], ziel["laenge"])) < routewerkzeug.SCHON_DA_M:
                friday_karten.route_markieren(k["id"], angekommen=True, veraltet=False, bis=jetzt + timedelta(minutes=ANGEKOMMEN_MIN))
                return
        wann = None
        if auftrag.get("wann"):
            try:
                gewuenscht = datetime.fromisoformat(auftrag["wann"])
                wann = gewuenscht if gewuenscht > jetzt else None          # ist die Zeit da, gilt „jetzt“
            except ValueError:
                wann = None
        ergebnis = await routen.alle((von["breite"], von["laenge"]), (ziel["breite"], ziel["laenge"]),
                                     verkehrsmittel=tuple(auftrag.get("modi") or routen.ALLE_MODI), wann=wann, jetzt=jetzt)
    except Exception as exc:                                    # noqa: BLE001 — die Live-Auffrischung darf nie die Übersicht kosten
        log.warning("Live-Route %s: %s: %s", k.get("id"), type(exc).__name__, exc)
        _veraltet(k, jetzt)
        return
    if not ergebnis["optionen"]:
        _veraltet(k, jetzt)
        return
    if any(o["modus"] == "auto" for o in ergebnis["optionen"]):
        hinweise.append("Autozeiten sind ohne aktuellen Verkehr gerechnet.")
    friday_karten.route_ersetzen(k["id"], {"start": von, "ziel": ziel, "optionen": ergebnis["optionen"], "schnellste": ergebnis["schnellste"],
                                           "ausfaelle": ergebnis["ausfaelle"], "hinweise": hinweise, "auftrag": auftrag},
                                 bis_mindestens=jetzt + timedelta(minutes=VERLAENGERN_MIN))


def _veraltet(k: dict, jetzt: datetime) -> None:
    if _alter_s(k, jetzt) > VERALTET_S and not k["daten"].get("veraltet"):
        friday_karten.route_markieren(k["id"], veraltet=True)


async def _lauf(faellige: list[dict]) -> None:
    for k in faellige:
        await auffrischen(k)


def _anstossen() -> None:
    global _aufgabe
    if _aufgabe and not _aufgabe.done():
        return
    jetzt = config.jetzt()
    faellige = [k for k in friday_karten.aktive_routen()
                if not k["daten"].get("angekommen") and _alter_s(k, jetzt) >= LIVE_S]
    if faellige:
        _aufgabe = asyncio.get_running_loop().create_task(_lauf(faellige))


async def karten() -> list[dict]:
    _anstossen()
    return []
