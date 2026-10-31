"""Verspätete Fahrten finden — auf Knopfdruck und im Hintergrund.

Eikes Wunsch (15.09.2026): ein „Suchen“-Knopf, der viele verspätete Fahrten zeigt,
vorzugsweise in Musterhausen, Bennigsen, Wennigsen, Hemmingen, Pattensen, Empelde,
Musterhausen und Weetzen. Findet sich dort nichts Beantragbares, die angrenzenden Orte,
und dann das ganze Netz (Haltestellen, die zusammen alle Linien abdecken).

Quelle ist die Abfahrtstafel der GVH-Auskunft (HAFAS), nicht die der ÜSTRA: Nur sie
gibt jeder Fahrt eine Kennung (`jid`), mit der sich danach der ganze Verlauf mit
allen Halten abrufen lässt. Je Ort genügen wenige Haltestellen — Bahnhof, ZOB,
Zentrum —, denn eine verspätete Fahrt trägt ihre Verspätung an jedem Halt.

Welche Fahrten zählen: Laut Bedingungen (Stand 01.06.2026) gilt die ÜSTRA-Garantie
„für alle Fahrten mit ÜSTRA Fahrausweis“ — auch S-Bahn und Regionalzug. Ein
früherer Stand hier schloss die S-Bahn aus; das beruhte auf EINER Formularsuche und
war falsch (Eike, 15.09.). Ausgeschlossen bleibt nur, wofür ein ÜSTRA-Fahrausweis
nicht gilt: Fernverkehr und sprinti.

Gemessen am 15.09.2026: Echtzeit tragen nur zeitnahe Fahrten; ältere verlieren sie.
Deshalb schaut die Suche 90 Minuten zurück und merkt sich Fahrten dauerhaft.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from kern import config
from kern.dienste import gvh, uestra
from kern.garantie import ablage

# Reihenfolge = Relevanz für Eike (16.09.2026: „Musterdorf Bennigsen Musterhausen Wennigsen Hemmingen …“).
ORTE = ("Musterdorf", "Bennigsen", "Musterhausen", "Wennigsen", "Hemmingen", "Pattensen", "Empelde", "Weetzen")
# Die Orte rundherum — gefragt wird dort nur, wenn in Eikes Orten nichts Beantragbares ist.
NACHBARN = ("Ronnenberg", "Barsinghausen", "Laatzen", "Sarstedt", "Völksen", "Eldagsen", "Holtensen",
            "Arnum", "Wilkenburg", "Harkenbleck", "Devese", "Hiddestorf", "Ohlendorf", "Jeinsen", "Schulenburg",
            "Koldingen", "Everloh", "Lenthe", "Ditterke", "Degersen", "Bredenbeck", "Linderte", "Vörie",
            "Wettbergen", "Badenstedt")
# Mit Wortgrenzen: sonst gewann „Musterhausen Schulzentrum Süd“ als „Zentrum“ (15.09.2026).
WICHTIG = re.compile(r"\b(bahnhof|zob|zentrum|mitte|rathaus|markt)\b", re.I)
PRO_ORT = 2
PRO_NACHBAR = 1
ZURUECK = timedelta(minutes=90)
VORAUS = timedelta(minutes=60)
GLEICHZEITIG = 4
ANZEIGEN_AB = 10              # „viele Fahrten mit Verspätung“ — beantragbar ist erst über 20
GARANTIE_AB = 20

# Wofür ein ÜSTRA-Fahrausweis nicht gilt: Fernverkehr und sprinti.
NICHT_BEANTRAGBAR = re.compile(r"\b(ice|ic|ec|ece|en|nj|rj|tgv|flx|flixtrain|sprinti|taxi)\b|fernverkehr|intercity|eurocity",
                               re.I)


S5 = re.compile(r"^S\s*5$", re.I)


def ist_s5(f: dict) -> bool:
    """Eikes meistgenutzte Bahn — dafür gibt es eine eigene Meldungsart und einen eigenen Filter."""
    return bool(S5.match((f.get("linie") or "").strip()))


def beantragbar(kategorie: str, linie: str = "") -> bool:
    """Die GVH-Auskunft führt Fernzüge als Kategorie „Zug“ — erkennbar erst an der
    Linie („ICE 1234“, „IC 2037“). Deshalb beides prüfen (16.09.2026)."""
    k = (kategorie or "").strip()
    return bool(k) and not NICHT_BEANTRAGBAR.search(k) and not NICHT_BEANTRAGBAR.match((linie or "").strip())


def _ortsrang(text: str) -> int:
    for i, ort in enumerate(ORTE):
        if re.search(rf"\b{re.escape(ort)}\b", text or "", re.I):
            return i
    if any(re.search(rf"\b{re.escape(ort)}\b", text or "", re.I) for ort in NACHBARN):
        return len(ORTE)
    return len(ORTE) + 1


def relevanz(f: dict) -> tuple:
    """Sortierung für Eike: erst was beantragbar ist (über 20 Minuten), dann nach
    seinen Orten in seiner Reihenfolge (Halt oder Richtung), dann die größere Verspätung."""
    rang = min(_ortsrang(f.get("halt", "")), _ortsrang(f.get("richtung", "")))
    return ((f.get("verspaetung") or 0) <= GARANTIE_AB, rang, -(f.get("verspaetung") or 0))


async def haltestellen_im_gebiet(orte=ORTE, pro_ort: int = PRO_ORT) -> list[dict]:
    """Je Ort die wichtigsten Haltestellen: der Ortsname selbst (oft der Knoten,
    etwa „Empelde“ mit der Stadtbahn 9), dann Bahnhof, ZOB, Zentrum …"""
    alle = await uestra.haltestellen()
    auswahl = []
    for ort in orte:
        im_ort = [h for h in alle if h.get("gid") and re.search(rf"\b{re.escape(ort)}\b", h["name"])]
        im_ort.sort(key=lambda h: (h["name"] != ort, not WICHTIG.search(h["name"]), len(h["name"])))
        auswahl += im_ort[:pro_ort]
    return auswahl


def ganzes_netz() -> list[dict]:
    """Die Haltestellen, die zusammen alle Linien abdecken (`data/uestra_abdeckung.json`)."""
    import json
    try:
        d = json.loads((config.daten_ordner() / "uestra_abdeckung.json").read_text(encoding="utf-8"))
        return [h for h in d.get("haltestellen", []) if h.get("gid") and h.get("name")]
    except (OSError, ValueError):
        return []


def oeffentlich(f: dict) -> dict:
    """Für Browser und Friday: kurze Kennung statt der 148 Zeichen, Zeiten als Text."""
    jetzt = config.jetzt()
    echt = f.get("echt")
    if isinstance(echt, str):
        echt = datetime.fromisoformat(echt)
    return {"id": ablage.kurz(f["jid"]), "linie": f["linie"], "kategorie": f.get("kategorie", ""),
            "richtung": f.get("richtung", ""), "halt": f.get("halt", ""),
            "plan": f["plan"].isoformat() if isinstance(f["plan"], datetime) else f["plan"],
            "echt": echt.isoformat() if echt else None, "verspaetung": f.get("verspaetung"),
            "vorbei": bool(echt and echt < jetzt), "bereich": f.get("bereich", "")}


async def abtasten(haltestellen: list[dict], mindest: int, zurueck: timedelta = ZURUECK,
                   voraus: timedelta = VORAUS) -> list[dict]:
    """Alle Tafeln abfragen, verspätete Fahrten sammeln — je Fahrt einmal, mit der
    höchsten gesehenen Verspätung."""
    jetzt = config.jetzt()
    von, bis = jetzt - zurueck, jetzt + voraus
    ampel = asyncio.Semaphore(GLEICHZEITIG)

    async def tafel(halt: dict):
        async with ampel:
            try:
                return halt, await gvh.tafel_roh(halt["gid"], von, anzahl=100)
            except Exception:
                return halt, []

    fahrten: dict[str, dict] = {}
    for halt, liste in await asyncio.gather(*(tafel(h) for h in haltestellen)):
        for f in liste:
            if f["plan"] > bis or f["faellt_aus"] or (f["verspaetung"] or 0) < mindest or not beantragbar(f["kategorie"], f["linie"]):
                continue
            alt = fahrten.get(f["jid"])
            if not alt or f["verspaetung"] > alt["verspaetung"]:
                fahrten[f["jid"]] = {**f, "halt": halt["name"]}
    return sorted(fahrten.values(), key=lambda f: -f["verspaetung"])


async def suchen(mindest: int = ANZEIGEN_AB) -> dict:
    """Der Knopf. Erst Eikes Orte; ist dort nichts über 20 Minuten, die Nachbarorte;
    dann das ganze Netz. Alles Gefundene wird gemerkt, damit die Fahrt später noch
    geöffnet werden kann."""
    stufen = [("deine Orte", lambda: haltestellen_im_gebiet()),
              ("Nachbarorte", lambda: haltestellen_im_gebiet(NACHBARN, PRO_NACHBAR)),
              ("ganzes Netz", lambda: _asynchron(ganzes_netz()))]
    gefunden: dict[str, dict] = {}
    bereich = ""
    for name, halte in stufen:
        bereich = name
        for f in await abtasten(await halte(), mindest):
            gefunden.setdefault(f["jid"], {**f, "bereich": name})
        if any(f["verspaetung"] > GARANTIE_AB for f in gefunden.values()):
            break
    a = ablage.standard()
    for f in gefunden.values():
        a.reise_merken(f)
    liste = sorted(gefunden.values(), key=relevanz)
    # Verläufe im Hintergrund sichern — die Suche soll dafür nicht warten.
    from kern.garantie import sammler
    _hintergrund.add(t := asyncio.create_task(sammler.verlaeufe_sichern(liste, 20)))
    t.add_done_callback(_hintergrund.discard)
    return {"fahrten": [oeffentlich(f) for f in liste], "bereich": bereich}


_hintergrund: set = set()


async def _asynchron(wert):
    return wert
