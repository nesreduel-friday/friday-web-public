"""Abfahrten im Blick — Friday sagt Bescheid, BEVOR Eike an der Haltestelle steht.

Eikes Wunsch (20.09.2026): „dass ich Nachrichten bekomme von der App, dass ein
Bus Verspätung hat oder so.“ Dafür braucht es zwei Dinge, die Friday vorher nicht
hatte: zu wissen, WELCHE Fahrt ihn angeht, und nachzusehen, ohne dass er fragt.

Eine Wache ist genau das: eine Haltestelle, eine Linie, ein Zeitfenster, an
bestimmten Wochentagen. Zum Beispiel „Bennigsen, Linie 300 Richtung Hannover,
werktags zwischen 7:10 und 7:40“. Ab `VORLAUF_MIN` vor dem Fenster schaut Friday
alle paar Minuten auf die Abfahrtstafel; ist eine passende Fahrt mindestens
`SCHWELLE_MIN` zu spät oder fällt sie aus, kommt eine Benachrichtigung.

Warum ausschließlich die ÜSTRA-Tafel und nicht der GVH-Fahrplan: Der GVH liefert
für viele Halte nur Plandaten, und Planzeiten sind nie verspätet — eine Wache
darauf würde nie etwas melden und wäre schlimmer als keine. Deshalb wird eine
Haltestelle beim ANLEGEN gegen die ÜSTRA-Liste geprüft und sonst abgelehnt.

Geschickt wird über `benachrichtigungen` unter der Art `fahrt`, also abschaltbar
wie alles andere — aber `dringend`, denn eine Warnung nach der Ruhezeit ist keine
Warnung mehr. Je Fahrt und Tag höchstens eine Nachricht; erst wenn es um
`NACHSCHLAG_MIN` schlimmer wird oder die Fahrt ganz ausfällt, kommt eine zweite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, time as tageszeit, timedelta

from kern import config, konnektoren
from kern.dienste import benachrichtigungen, uestra

log = logging.getLogger("friday.wache")

KONNEKTOR = "gvh"
DATEI = "wachen.json"
ART = "fahrt"

VORLAUF_MIN = 30        # so lange vor dem Fenster wird schon geschaut
NACHLAUF_MIN = 5        # und so lange danach noch
TAKT_S = 180            # alle drei Minuten, aber nur wenn eine Wache offen ist
RUHE_TAKT_S = 600       # sonst seltener nachsehen, ob eine aufgeht
SCHWELLE_MIN = 5        # ab so vielen Minuten heißt es Verspätung
NACHSCHLAG_MIN = 10     # so viel schlimmer muss es werden für eine zweite Nachricht
MAX_WACHEN = 12
ABFAHRTEN = 25          # so viele Abfahrten von der Tafel ansehen
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_sperre = threading.Lock()
# Was heute schon gemeldet wurde: Fahrt → gemeldete Verspätung. Nur im Speicher;
# nach einem Neustart meldet Friday eine anhaltende Verspätung noch einmal — das
# ist besser herum als still zu bleiben.
_gemeldet: dict[str, int] = {}
_gemeldet_tag = ""


class WachenFehler(ValueError):
    """Vorlesbar: sagt, was schiefliegt."""


def _pfad():
    return config.daten_ordner() / DATEI


def alle() -> list[dict]:
    try:
        d = json.loads(_pfad().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _speichern(liste: list[dict]) -> None:
    ziel = _pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    with _sperre:
        fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(liste, f, ensure_ascii=False, indent=1)
        os.chmod(neu, 0o600)
        os.replace(neu, ziel)


def _uhr(text: str) -> tageszeit:
    try:
        stunde, minute = (int(t) for t in str(text).strip().split(":")[:2])
        return tageszeit(stunde, minute)
    except (TypeError, ValueError):
        raise WachenFehler(f"„{text}“ ist keine Uhrzeit. Ich brauche sie als 07:10.") from None


def _tage_deuten(tage) -> list[int]:
    """Wochentage als Zahlen (0 = Montag). Leer heißt: jeden Tag."""
    if tage is None:
        return []
    if isinstance(tage, str):
        wort = tage.casefold()
        if "werktag" in wort or "schultag" in wort or "wochentag" in wort:
            return [0, 1, 2, 3, 4]
        if "wochenende" in wort:
            return [5, 6]
        if "täglich" in wort or "taeglich" in wort or "jeden" in wort:
            return []
        tage = [t.strip() for t in tage.replace(",", " ").split()]
    raus = []
    for t in tage or []:
        if isinstance(t, int) and 0 <= t <= 6:
            raus.append(t)
            continue
        kurz = str(t)[:2].capitalize()
        if kurz in WOCHENTAGE:
            raus.append(WOCHENTAGE.index(kurz))
    return sorted(set(raus))


def _tage_text(tage: list[int]) -> str:
    if not tage:
        return "täglich"
    if tage == [0, 1, 2, 3, 4]:
        return "werktags"
    if tage == [5, 6]:
        return "am Wochenende"
    return ", ".join(WOCHENTAGE[t] for t in tage)


def text(w: dict) -> str:
    """Eine Wache in einem Satz — so liest Friday sie vor."""
    richtung = f" Richtung {w['richtung']}" if w.get("richtung") else ""
    linie = f"Linie {w['linie']}" if w.get("linie") else "alles"
    return (f"{linie}{richtung} ab {w['halt']}, {_tage_text(w['tage'])} zwischen "
            f"{w['ab']} und {w['bis']} Uhr (ab {w['schwelle']} Min Verspätung)")


async def anlegen(halt: str, linie: str = "", richtung: str = "", ab: str = "", bis: str = "",
                  tage=None, schwelle: int = SCHWELLE_MIN) -> dict:
    """Eine Wache anlegen. Die Haltestelle muss die ÜSTRA kennen — sonst gäbe es
    keine Echtzeit, und die Wache wäre eine leere Zusage."""
    if len(alle()) >= MAX_WACHEN:
        raise WachenFehler(f"Mehr als {MAX_WACHEN} Wachen behalte ich nicht im Blick. "
                           "Nimm erst eine weg.")
    von, nach = _uhr(ab or "07:00"), _uhr(bis or "08:00")
    if von >= nach:
        raise WachenFehler("Das Fenster muss vorwärts laufen — Beginn vor Ende.")
    try:
        schwelle = max(1, min(int(schwelle), 60))
    except (TypeError, ValueError):
        schwelle = SCHWELLE_MIN
    try:
        stand = await uestra.abfahrten(halt, 1)
    except uestra.Mehrdeutig as exc:
        raise WachenFehler(str(exc)) from None
    except uestra.Unbekannt:
        raise WachenFehler(f"Die Haltestelle „{halt}“ hat die ÜSTRA nicht in ihrer Echtzeit-Liste. "
                           "Ohne Echtzeit könnte ich keine Verspätung sehen — nenn mir bitte eine "
                           "andere Haltestelle in der Nähe.") from None
    except Exception as exc:
        raise WachenFehler(f"Die Abfahrtstafel antwortet gerade nicht ({type(exc).__name__}). "
                           "Probier es gleich noch einmal.") from None

    liste = alle()
    kennung = str(max((int(w.get("id", 0)) for w in liste), default=0) + 1)
    w = {"id": kennung, "halt": stand["haltestelle"], "linie": str(linie or "").strip()[:20],
         "richtung": str(richtung or "").strip()[:60], "ab": f"{von:%H:%M}", "bis": f"{nach:%H:%M}",
         "tage": _tage_deuten(tage), "schwelle": schwelle, "aktiv": True}
    liste.append(w)
    _speichern(liste)
    log.info("Wache angelegt: %s", text(w))
    return w


def entfernen(kennung: str) -> dict | None:
    liste = alle()
    bleibt = [w for w in liste if str(w.get("id")) != str(kennung)]
    if len(bleibt) == len(liste):
        return None
    weg = next(w for w in liste if str(w.get("id")) == str(kennung))
    _speichern(bleibt)
    return weg


def stellen(kennung: str, aktiv: bool) -> dict | None:
    liste = alle()
    for w in liste:
        if str(w.get("id")) == str(kennung):
            w["aktiv"] = bool(aktiv)
            _speichern(liste)
            return w
    return None


def finden(wunsch: str) -> list[dict]:
    """Wachen, die zu einer Beschreibung passen — nach Haltestelle oder Linie."""
    gesucht = (wunsch or "").casefold().strip()
    if not gesucht:
        return []
    return [w for w in alle()
            if gesucht in w["halt"].casefold() or (w.get("linie") and gesucht == w["linie"].casefold())]


# ---------- Nachsehen ----------

def heute_dran(w: dict, jetzt: datetime) -> bool:
    return not w.get("tage") or jetzt.weekday() in w["tage"]


def offen(w: dict, jetzt: datetime) -> bool:
    """Ist das Fenster dieser Wache gerade auf (mit Vor- und Nachlauf)?"""
    if not w.get("aktiv", True) or not heute_dran(w, jetzt):
        return False
    tag = jetzt.date()
    von = datetime.combine(tag, _uhr(w["ab"]), jetzt.tzinfo) - timedelta(minutes=VORLAUF_MIN)
    nach = datetime.combine(tag, _uhr(w["bis"]), jetzt.tzinfo) + timedelta(minutes=NACHLAUF_MIN)
    return von <= jetzt <= nach


def _linie_passt(w: dict, a: dict) -> bool:
    if not w.get("linie"):
        return True
    return str(a.get("linie", "")).casefold() == w["linie"].casefold()


def _richtung_passt(w: dict, a: dict) -> bool:
    if not w.get("richtung"):
        return True
    return w["richtung"].casefold() in str(a.get("richtung", "")).casefold()


def betroffen(w: dict, abfahrten: list[dict], jetzt: datetime) -> list[dict]:
    """Die Abfahrten dieser Wache, die Ärger machen — im Fenster, zu spät oder aus.

    Verglichen wird die PLANzeit mit dem Fenster: Eike will die Bahn von 7:20
    nehmen; dass sie erst um 7:50 kommt, ist genau der Grund für die Nachricht,
    und nach der Prognose fiele sie aus dem Fenster heraus."""
    tag = jetzt.date()
    von = datetime.combine(tag, _uhr(w["ab"]), jetzt.tzinfo)
    bis = datetime.combine(tag, _uhr(w["bis"]), jetzt.tzinfo)
    raus = []
    for a in abfahrten:
        plan = a.get("plan")
        if not isinstance(plan, datetime) or not (von <= plan <= bis):
            continue
        if not _linie_passt(w, a) or not _richtung_passt(w, a):
            continue
        if a.get("faellt_aus") or (a.get("verspaetung") or 0) >= w.get("schwelle", SCHWELLE_MIN):
            raus.append(a)
    return raus


def _tag_wechseln(jetzt: datetime) -> None:
    global _gemeldet_tag
    heute = jetzt.date().isoformat()
    if heute != _gemeldet_tag:
        with _sperre:
            _gemeldet.clear()
            _gemeldet_tag = heute


def _neu_melden(schluessel: str, verspaetung: int, faellt_aus: bool) -> bool:
    """Schon gemeldet? Dann nur wieder, wenn es deutlich schlimmer geworden ist.

    Ohne das käme alle drei Minuten dieselbe Nachricht — das Handy wäre nach
    einer halben Stunde unbenutzbar."""
    with _sperre:
        vorher = _gemeldet.get(schluessel)
        jetzt_wert = 999 if faellt_aus else verspaetung
        if vorher is not None and jetzt_wert - vorher < NACHSCHLAG_MIN:
            return False
        _gemeldet[schluessel] = jetzt_wert
        return True


def _nachricht(w: dict, a: dict) -> tuple[str, str]:
    linie = a.get("linie") or w.get("linie") or "Deine Fahrt"
    richtung = f" Richtung {a['richtung']}" if a.get("richtung") else ""
    plan = a["plan"]
    if a.get("faellt_aus"):
        return (f"{linie} um {plan:%H:%M} fällt aus",
                f"Ab {w['halt']}{richtung}. Schau nach der nächsten Verbindung.")
    versp = a.get("verspaetung") or 0
    prognose = a.get("prognose")
    spaeter = f", kommt gegen {prognose:%H:%M}" if isinstance(prognose, datetime) else ""
    return (f"{linie} um {plan:%H:%M} ist {versp} Min später",
            f"Ab {w['halt']}{richtung}{spaeter}.")


async def pruefen(jetzt: datetime | None = None) -> dict:
    """Einmal alle offenen Wachen ansehen. Gibt zurück, was getan wurde."""
    jetzt = jetzt or config.jetzt()
    _tag_wechseln(jetzt)
    dran = [w for w in alle() if offen(w, jetzt)]
    if not dran:
        return {"wachen": 0, "gemeldet": 0, "fehler": ""}
    # Eine Haltestelle nur einmal abfragen, auch wenn zwei Wachen daran hängen.
    tafeln: dict[str, list[dict]] = {}
    gemeldet, fehler = 0, ""
    for w in dran:
        if w["halt"] not in tafeln:
            try:
                stand = await uestra.abfahrten(w["halt"], ABFAHRTEN)
                tafeln[w["halt"]] = stand["abfahrten"]
            except Exception as exc:
                fehler = f"{w['halt']}: {type(exc).__name__}"
                tafeln[w["halt"]] = []
        for a in betroffen(w, tafeln[w["halt"]], jetzt):
            schluessel = f"{w['id']}-{a['plan']:%H:%M}-{a.get('linie', '')}"
            if not _neu_melden(schluessel, a.get("verspaetung") or 0, bool(a.get("faellt_aus"))):
                continue
            titel, rumpf = _nachricht(w, a)
            await asyncio.to_thread(benachrichtigungen.senden, ART, titel, rumpf,
                                    "/#sprechen", f"wache-{schluessel}", True)
            gemeldet += 1
            log.info("Wache gemeldet: %s", titel)
    return {"wachen": len(dran), "gemeldet": gemeldet, "fehler": fehler}


async def dauerlauf() -> None:
    """Im Hintergrund nachsehen — dicht, solange ein Fenster offen ist, sonst träge."""
    await asyncio.sleep(90)
    while True:
        pause = RUHE_TAKT_S
        try:
            if konnektoren.benutzbar(KONNEKTOR) and benachrichtigungen.an(ART):
                jetzt = config.jetzt()
                if any(offen(w, jetzt) for w in alle()):
                    await pruefen(jetzt)
                    pause = TAKT_S
        except Exception as exc:                  # ein Fehler darf den Lauf nie töten
            log.warning("Wachenlauf gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(pause)
