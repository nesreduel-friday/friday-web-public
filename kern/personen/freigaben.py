"""Freigaben: Eike erlaubt Friday, einer Person etwas zu sagen. Nur Kopiertes kann sie je erfahren.

Eine Freigabe ist ein Text, den Eike formuliert („Eike hat im Oktober Geburtstag“). Er wird als eigene Episode in die
Graph-Gruppe der Person eingespeist (`freigabe-<pid>-<fid>`); nur was dort steht, findet Friday bei der Person.
Widerruf gilt sofort (Status) und löscht die Kopie in der Gruppe der Person — nie etwas in Eikes Gruppe. War die
Episode noch nicht verarbeitet, versucht `nachlauf` es später wieder, bis sie auftaucht und gelöscht ist."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from kern.gedaechtnis import graph
from kern.personen import PersonenFehler, register

log = logging.getLogger("friday.personen")


async def freigeben(pid: str, text: str, anlass: str = "") -> dict:
    person = register.holen(pid)
    if not person:
        raise PersonenFehler("Diese Person gibt es nicht.")
    f = register.freigabe_anlegen(pid, text, anlass)  # wirft bei leerem Text
    ok = await graph.einspeisen(f["episode"], f["text"], f"Von Eike für {person['name']} freigegeben",
                                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                anweisungen=graph.anweisungen_fuer_freigabe(person["name"]), gruppe=pid)
    if not ok:
        register.freigabe_setzen(pid, f["id"], status="fehlgeschlagen")
        raise PersonenFehler("Der Wissensgraph nimmt gerade nichts an. Es wurde nichts freigegeben — bitte später nochmal.")
    return register.freigabe_setzen(pid, f["id"], status="aktiv")


async def widerrufen(pid: str, fid: str) -> dict:
    f = next((x for x in register.freigaben(pid) if x["id"] == fid), None)
    if not f:
        raise PersonenFehler("Diese Freigabe gibt es nicht.")
    if f["status"] == "widerrufen":
        return f
    if f["status"] != "aktiv":
        raise PersonenFehler("Diese Freigabe war nie aktiv — es gibt nichts zu widerrufen.")
    f = register.freigabe_setzen(pid, fid, status="widerrufen",  # gilt ab jetzt, auch wenn das Löschen nicht geht
                                 widerrufen_zeit=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return await _loeschen(pid, f)


async def _loeschen(pid: str, f: dict) -> dict:
    if f.get("geloescht"):
        return f
    ergebnis = await graph.episode_loeschen(f["episode"], pid)
    if ergebnis is True:
        return register.freigabe_setzen(pid, f["id"], geloescht=True, loeschen="")
    # None: Episode (noch) nicht da; False: Fehler — Fehler geben nie auf, „nicht da“ nur nach Ablauf der Frist.
    return register.freigabe_setzen(pid, f["id"], loeschen="nicht_da" if ergebnis is None else "fehler")


NACHLAUF_TAGE = 7  # so lange hatte Graphiti Zeit, eine Episode zu verarbeiten; danach gibt es sie nicht mehr


def _alter(zeit: str | None) -> timedelta | None:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(zeit)
    except (TypeError, ValueError):
        return None


def _aufgegeben(f: dict) -> bool:
    """Widerrufene: erst sieben Tage nach dem WIDERRUF und nur, wenn die Episode zuletzt „nicht da“ war (bei Fehlern
    wird weiter versucht). Nie bestätigte Freigaben (fehlgeschlagen/wartet): sieben Tage nach dem Anlegen."""
    if f["status"] == "widerrufen":
        alter = _alter(f.get("widerrufen_zeit") or f.get("zeit"))
        return f.get("loeschen") == "nicht_da" and alter is not None and alter > timedelta(days=NACHLAUF_TAGE)
    alter = _alter(f.get("zeit"))
    return alter is not None and alter > timedelta(days=NACHLAUF_TAGE)


def suche_gesperrt(pid: str) -> bool:
    """Steht bei dieser Person noch eine widerrufene Kopie im Graphen? Dann darf ihre Graph-Suche nicht laufen
    (fail-closed): Der Widerruf gilt sofort, das Löschen im Graphen erst, wenn es gelungen ist."""
    return any(f["status"] == "widerrufen" and not f.get("geloescht") for f in register.freigaben(pid))


async def nachlauf() -> int:
    """Widerrufene Freigaben — und solche, die nie als aktiv bestätigt wurden, deren Episode aber trotzdem im Graphen
    angekommen sein kann (Antwort verloren, Neustart) —, deren Kopie noch nicht gelöscht ist: erneut löschen.
    Gibt die Zahl der jetzt gelöschten zurück."""
    n = 0
    for person in register.liste():
        for f in register.freigaben(person["id"]):
            if f["status"] == "aktiv" or f.get("geloescht") or _aufgegeben(f):
                continue
            if (await _loeschen(person["id"], f)).get("geloescht"):
                n += 1
    return n
