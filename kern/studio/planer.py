"""Tagesplaner für den YouTube-Kanal — ein Video pro Tag, dafür mit Sorgfalt.

Eikes Entscheidung, und sie ist messbar richtig: An Tagen mit mehreren
Veröffentlichungen brachen einzelne Videos auf 16 oder 1 Aufruf ein, obwohl ihre
Rückhaltung im Mittelfeld lag (31.08.: 986 und 16 Aufrufe am selben Tag). Nicht
das Thema war schlechter, die Schlagzahl war zu hoch.

Ablauf: Leistungsdaten holen → Thema wählen, das weder wiederholt noch schwer
bebilderbar ist → Auftrag für den Renderer ablegen → wenn das Video fertig ist,
Eike benachrichtigen. Veröffentlicht wird NIE von hier aus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from kern import config, konnektoren
from kern.dienste import benachrichtigungen, studio
from kern.schule import briefe

log = logging.getLogger("friday.studio")

KONNEKTOR = "studio"
TAKT_S = 600                    # alle zehn Minuten nachsehen, ob heute schon eines läuft
MAX_HISTORIE = 200

SCHEMA = {"type": "OBJECT", "properties": {
    "thema": {"type": "STRING"},
    "aufhaenger": {"type": "STRING"},
    "titel": {"type": "STRING"},
    "beschreibung": {"type": "STRING"},
    "schlagworte": {"type": "ARRAY", "items": {"type": "STRING"}},
    "warum": {"type": "STRING"},
}, "required": ["thema", "aufhaenger", "titel", "beschreibung", "schlagworte", "warum"]}


def _ordner(name: str):
    pfad = config.daten_ordner() / "studio" / name
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def _historiedatei():
    return config.daten_ordner() / "studio" / "themen.json"


def historie() -> list[dict]:
    try:
        return json.loads(_historiedatei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _historie_schreiben(eintraege: list[dict]) -> None:
    datei = _historiedatei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    neu = datei.with_suffix(".neu")
    neu.write_text(json.dumps(eintraege[-MAX_HISTORIE:], ensure_ascii=False, indent=2), encoding="utf-8")
    neu.replace(datei)


def _worte(text: str) -> set[str]:
    sauber = "".join(z if z.isalnum() else " " for z in (text or "").casefold())
    return {w for w in sauber.split() if len(w) > 3}


def schon_dagewesen(thema: str, frueher: list[str]) -> bool:
    """Ist das Thema — oder sein Gegenstand — schon dran gewesen?

    Jarvis verglich nur Wortüberlappung. Das reichte nicht: Auf dem Kanal gibt es
    vier Tauben-Videos, drei über Bienen und zwei über Seeotter. Deshalb zählt
    hier zusätzlich JEDES tragende Wort für sich — kommt „pigeons" schon vor,
    ist ein weiteres Taubenthema keine neue Idee.
    """
    neu = _worte(thema)
    if not neu:
        return True
    for alt in frueher:
        alte = _worte(alt)
        if not alte:
            continue
        if len(neu & alte) / max(1, len(neu | alte)) >= 0.45:
            return True
    # Gegenstand schon belegt: ein langes Hauptwort, das nur in einem Thema
    # vorkommen sollte (Tierart, Ort, Gerät).
    belegt = set()
    for alt in frueher:
        belegt |= {w for w in _worte(alt) if len(w) >= 5}
    return bool({w for w in neu if len(w) >= 5} & belegt)


async def thema_waehlen() -> dict:
    """Ein Thema samt Titel und Beschreibung — auf Grundlage echter Zahlen."""
    daten = await studio.leistung(120)
    zahlen = studio.leistungstext(daten)
    frueher = [e.get("thema", "") for e in historie()]
    gemacht = "\n".join(f"- {t}" for t in frueher[-60:]) or "noch keins"

    anweisung = (
        "Du planst EIN Kurzvideo (unter 60 Sekunden, hochkant) für einen laufenden "
        "englischsprachigen YouTube-Kanal über überraschende Tatsachen. Antworte auf Englisch, "
        "außer bei „warum“ — das schreibst du auf Deutsch.\n"
        "WAS ZÄHLT: Wie viel Prozent eines Videos geschaut werden, entscheidet, ob YouTube es "
        "weiter zeigt. Ein Thema trägt nur, wenn es EINEN konkreten Mechanismus hat, der sich in "
        "wenigen Sätzen erklären lässt, und wenn die Überraschung im ersten Satz steht — unter "
        "zwölf Wörtern, ohne Anlauf.\n"
        "BILDER: Das Thema muss sich mit ECHTEM Stockmaterial zeigen lassen (Pexels, Pixabay). "
        "Nichts Mikroskopisches, nichts Seltenes, nichts rein Gedankliches — sonst gibt es keine "
        "Aufnahmen. KI-erzeugte Bilder sind ausgeschlossen.\n"
        "NIE WIEDERHOLEN: Weder ein Thema aus der Liste noch denselben Gegenstand mit anderem "
        "Blickwinkel. Kam eine Tierart schon vor, ist sie verbraucht.\n"
        "Die Beschreibung nennt in drei bis fünf Sätzen, worum es geht."
    )
    frage = (f"ZAHLEN DIESES KANALS — echte Messwerte, richte dich danach:\n{zahlen}\n\n"
             f"SCHON GEMACHT (nicht wiederholen, auch nicht den Gegenstand):\n{gemacht}")

    plan, modell = await briefe._gemini([{"text": frage}], anweisung=anweisung, schema=SCHEMA)
    thema = str(plan.get("thema") or "").strip()
    if not thema:
        raise RuntimeError("Der Plan kam ohne Thema zurück.")
    if schon_dagewesen(thema, frueher):
        raise RuntimeError(f"Das Modell schlug ein Thema vor, das es schon gab: {thema}")
    plan["modell"] = modell
    return plan


async def auftrag_ablegen(plan: dict) -> str:
    """Den Renderauftrag für den Studio-Container hinlegen. Gibt die Kennung zurück."""
    kennung = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    schluessel = {}
    try:
        schluessel = json.loads((config.daten_ordner() / "studio_schluessel.json")
                                .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Keine Studio-Schlüssel hinterlegt — der Renderer wird scheitern")

    auftrag = {"thema": plan["thema"], "titel": plan.get("titel", ""),
               "beschreibung": plan.get("beschreibung", ""),
               "schlagworte": plan.get("schlagworte") or [],
               "aufhaenger": plan.get("aufhaenger", ""), "warum": plan.get("warum", ""),
               "untertitel": True, "schluessel": schluessel,
               "geplant": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    datei = _ordner("auftraege") / f"{kennung}.json"
    neu = datei.with_suffix(".neu")
    neu.write_text(json.dumps(auftrag, ensure_ascii=False, indent=2), encoding="utf-8")
    neu.replace(datei)

    eintraege = historie()
    eintraege.append({"thema": plan["thema"], "titel": plan.get("titel", ""),
                      "kennung": kennung, "wann": auftrag["geplant"]})
    _historie_schreiben(eintraege)
    return kennung


def _heute_schon_geplant() -> bool:
    heute = datetime.now(timezone.utc).date().isoformat()
    return any(str(e.get("wann", ""))[:10] == heute for e in historie())


def _laeuft_schon() -> bool:
    return any(_ordner("auftraege").glob("*.json"))


async def einmal_planen() -> str:
    """Ein Video planen und in Auftrag geben. Meldet, was geschehen ist."""
    if not konnektoren.benutzbar(KONNEKTOR):
        return "Das Studio ist nicht freigegeben."
    if _laeuft_schon():
        return "Es läuft schon ein Auftrag."
    if _heute_schon_geplant():
        return "Für heute ist schon eins geplant."
    plan = await thema_waehlen()
    kennung = await auftrag_ablegen(plan)
    log.info("Video geplant: %s (%s)", plan["thema"], kennung)
    return f"Geplant: {plan['thema']} ({kennung})"


def _gemeldet() -> set[str]:
    try:
        return set(json.loads((config.daten_ordner() / "studio" / "gemeldet.json")
                              .read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _gemeldet_merken(kennungen: set[str]) -> None:
    datei = config.daten_ordner() / "studio" / "gemeldet.json"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(sorted(kennungen)), encoding="utf-8")


async def fertige_melden() -> int:
    """Eike benachrichtigen, sobald ein Video auf ihn wartet. Jedes nur einmal."""
    schon = _gemeldet()
    neu = 0
    for datei in _ordner("fertig").glob("*.json"):
        if datei.stem in schon:
            continue
        try:
            d = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("status") == "fertig":
            titel = d.get("titel") or d.get("thema", "Ein Video")
            await asyncio.to_thread(benachrichtigungen.senden, "studio", "Video fertig",
                                    f"„{titel}“ wartet auf dich.", "/#studio", f"studio-{datei.stem}")
            neu += 1
        elif d.get("status") == "fehler":
            grund = str(d.get("fehler") or "").splitlines()[0][:80]
            await asyncio.to_thread(benachrichtigungen.senden, "studio", "Video nicht fertig geworden",
                                    grund, "/#studio", f"studio-{datei.stem}")
            neu += 1
        schon.add(datei.stem)
    if neu:
        _gemeldet_merken(schon)
    return neu


async def dauerlauf() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            if konnektoren.benutzbar(KONNEKTOR):
                await fertige_melden()
                # Nur tagsüber planen: Ein Video, das nachts fertig wird, weckt
                # niemanden — und Eike soll es am selben Tag noch freigeben können.
                if 8 <= config.jetzt().hour < 18:
                    await einmal_planen()
        except Exception:
            log.exception("Studio-Tageslauf gescheitert")
        await asyncio.sleep(TAKT_S)
